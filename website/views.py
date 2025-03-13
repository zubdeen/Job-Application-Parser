from flask import Flask, request, render_template, redirect, url_for
from dotenv import load_dotenv
import os
import boto3
from botocore.exceptions import ClientError
from botocore.exceptions import NoCredentialsError
from werkzeug.utils import secure_filename
import docx
import pdfplumber
import re  # For parsing text
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
from datetime import datetime
import time
from datetime import datetime, timedelta, timezone
import schedule
from datetime import datetime, timedelta

# Load environment variables from .env file
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Configure upload folder and allowed file extensions
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# AWS S3 credentials
AWS_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY')
AWS_SECRET_KEY = os.getenv('AWS_SECRET_KEY')
AWS_REGION = os.getenv('AWS_REGION')
SENDER_EMAIL = os.getenv('SENDER_EMAIL')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

# Helper function to check file extensions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Function to upload file to S3
def upload_to_s3(file_path, bucket_name, object_name):
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    try:
        s3.upload_file(file_path, bucket_name, object_name, ExtraArgs={'ACL': 'public-read'})
        print(f"Upload successful: {object_name}")
        return f"https://{bucket_name}.s3.amazonaws.com/{object_name}"
    except FileNotFoundError:
        print("The file was not found.")
        return None
    except NoCredentialsError:
        print("Credentials not available.")
        return None

def extract_text_from_file(file_path, file_extension):
    if file_extension == 'pdf':
        return extract_text_from_pdf(file_path)
    elif file_extension == 'docx':
        return extract_text_from_docx(file_path)
    else:
        raise ValueError("Unsupported file format")

# Function to extract text from PDF
def extract_text_from_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ''
    return text

def extract_text_from_docx(file_path):
    doc = docx.Document(file_path)
    text = ""
    for paragraph in doc.paragraphs:
        text += paragraph.text + "\n"
    return text

def extract_resume_sections(text):
    """
    Extract multiple sections from a resume text using a more robust approach.
    Returns a dictionary with each section's content.
    """
    # Common section headers in resumes
    section_headers = {
        "education": ["education", "academic background", "degrees", "academic history"],
        "qualifications": ["skills", "qualifications", "technical skills", "certifications", "expertise"],
        "experience": ["projects", "experience", "work experience", "employment history", "internship"],
        "contact": ["contact", "personal information", "contact details"],
        "summary": ["summary", "professional summary", "profile", "objective"]
    }

    # Convert text to lowercase for case-insensitive matching
    text_lower = text.lower()
    lines = text.split('\n')

    # Find the starting indices of each section
    section_indices = {}
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for section, keywords in section_headers.items():
            # Use regex to match whole words only
            for keyword in keywords:
                if re.search(r'\b' + re.escape(keyword) + r'\b', line_lower):
                    section_indices[section] = i
                    break

    # Sort sections by their position in the text
    sorted_sections = sorted(section_indices.items(), key=lambda x: x[1])

    # Extract content for each section
    results = {}
    for i, (section, start_idx) in enumerate(sorted_sections):
        # Determine end index (start of next section or end of text)
        end_idx = len(lines)
        if i < len(sorted_sections) - 1:
            end_idx = sorted_sections[i + 1][1]

        # Extract content (skip the header line)
        content = lines[start_idx + 1:end_idx]
        # Remove leading/trailing empty lines
        while content and content[0].strip() == "":
            content.pop(0)
        while content and content[-1].strip() == "":
            content.pop()

        results[section] = content

    return results

def extract_education(text):
    """Extract education section from resume text."""
    sections = extract_resume_sections(text)
    return sections.get("education", [])

def extract_qualifications(text):
    """Extract qualifications section from resume text."""
    sections = extract_resume_sections(text)
    return sections.get("qualifications", [])

def extract_projects(text):
    """Extract projects/experience section from resume text."""
    sections = extract_resume_sections(text)
    return sections.get("experience", [])

def extract_contact_info(text):
    """Extract contact information section from resume text."""
    sections = extract_resume_sections(text)
    return sections.get("contact", [])

def extract_personal_info(text):
    # First try to find structured info with regex
    email = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    phone = re.search(r'(\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4})', text)
    name = re.search(r"Name:\s*(.*)", text, re.IGNORECASE)

    # Try to get more info from the contact section if it exists
    contact_section = extract_contact_info(text)
    contact_text = "\n".join(contact_section)

    # If no email found yet, try in contact section
    if not email and contact_section:
        email = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', contact_text)

    # If no phone found yet, try in contact section
    if not phone and contact_section:
        phone = re.search(r'(\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4})', contact_text)

    # If still no name, try to find it at the beginning of the document
    if not name:
        # Often the name is at the very top of the resume
        first_lines = text.split('\n')[:3]  # Check first 3 lines
        for line in first_lines:
            if line.strip() and not re.search(r'@|www|\d{3}', line):  # Avoid lines with emails, websites, or phone numbers
                name = line.strip()
                break

    personal_info = {
        "name": name.group(1) if hasattr(name, 'group') else name,
        "email": email.group(0) if email else None,
        "phone": phone.group(0) if phone else None
    }
    return personal_info

# Function to send follow-up email
def send_follow_up_email(applicant_email, time_zone):
    ses_client = boto3.client('ses',
                              region_name=AWS_REGION,
                              aws_access_key_id=AWS_ACCESS_KEY,
                              aws_secret_access_key=AWS_SECRET_KEY)

    subject = "Your CV is under review"
    body_text = "Thank you for submitting your CV. It is currently under review."

    try:
        response = ses_client.send_email(
            Destination={
                'ToAddresses': [
                    applicant_email,
                ],
            },
            Message={
                'Body': {
                    'Text': {
                        'Charset': 'UTF-8',
                        'Data': body_text,
                    },
                },
                'Subject': {
                    'Charset': 'UTF-8',
                    'Data': subject,
                },
            },
            Source=SENDER_EMAIL,
        )
        print(f"Email sent to {applicant_email}. Message ID: {response['MessageId']}")
    except ClientError as e:
        print(f"Failed to send email: {e.response['Error']['Message']}")

def schedule_follow_up(applicant_email):
    # Set the fixed time for the email (e.g., 10:00 AM )
    target_utc_time = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)

    # Calculate the delay until the target time
    delay_seconds = (target_utc_time - datetime.now(timezone.utc)).total_seconds()

    if delay_seconds > 0:
        schedule.every(delay_seconds).seconds.do(send_follow_up_email, applicant_email)
        print(f"Email scheduled for {applicant_email} at {target_utc_time} UTC.")
    else:
        print("Scheduled time has already passed.")


def save_to_google_sheets(data):
    try:
        # Get the JSON string directly from .env
        credentials_json = os.getenv('GOOGLE_CREDENTIALS_JSON')

        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(credentials_json), scope)
        client = gspread.authorize(creds)

        sheet_id = "1L_tHvB5PTru_d8CWyr3dDlsGvYNPXbMn9PsU1qv5m00"
        spreadsheet = client.open_by_key(sheet_id)
        print(f"Opened Spreadsheet: {spreadsheet.title}")

        worksheets = spreadsheet.worksheets()
        print("Available Sheets:", [ws.title for ws in worksheets])

        sheet = spreadsheet.sheet1

        # Debug: Check the data before appending
        print("Data being sent to Google Sheets:", data)
        print("Type of data:", type(data))

        # Append row
        sheet.append_row(data)
        print("Row added successfully!")
    except Exception as e:
        print(f"Error while adding row to Google Sheets: {e}")

# Function to send webhook
def send_webhook(payload, candidate_email):
    url = "https://rnd-assignment.automations-3d6.workers.dev/"
    headers = {
        "X-Candidate-Email": candidate_email,
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()  # Raise an exception for HTTP errors
        print(f"Webhook response: {response.status_code}, {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Webhook request failed: {e}")

# Route for the form
@app.route('/', methods=['GET', 'POST'])
def submit_form():
    if request.method == 'POST':
        # Get form data
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        cv = request.files['cv']

        # Validate file
        if cv and allowed_file(cv.filename):
            # Secure the filename and save it temporarily
            filename = secure_filename(cv.filename)
            file_extension = filename.rsplit('.', 1)[1].lower()  # Get file extension
            cv_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            cv.save(cv_path)

            # Upload the CV to S3 and get the public link
            cv_public_link = upload_to_s3(cv_path, S3_BUCKET_NAME, filename)
            if cv_public_link:
                print(f"CV uploaded successfully. Public link: {cv_public_link}")
                # Extract text from the CV
                cv_text = extract_text_from_file(cv_path, file_extension)
                # print(f"Extracted text: {cv_text}")

                # Extract specific sections
                personal_info = extract_personal_info(cv_text)
                education = extract_education(cv_text)
                qualifications = extract_qualifications(cv_text)
                projects = extract_projects(cv_text)

                # Print extracted sections for debugging
                print("Personal Info:", personal_info)
                print("Education:", education)
                print("Qualifications:", qualifications)
                print("Projects:", projects)

                # Example usage
                data = [
                    personal_info.get("name", ""),
                    personal_info.get("email", ""),
                    personal_info.get("phone", ""),
                    "\n".join(education),  # Combine education list into a single string
                    "\n".join(qualifications),  # Combine qualifications list into a single string
                    "\n".join(projects),  # Combine projects list into a single string
                    cv_public_link
                ]
                print("Final Data for Google Sheets:", data)
                save_to_google_sheets(data)

                # Prepare the payload for the webhook
                payload = {
                    "cv_data": {
                        "personal_info": personal_info,
                        "education": education,
                        "qualifications": qualifications,
                        "projects": projects,
                        "cv_public_link": cv_public_link
                    },
                    "metadata": {
                        "applicant_name": name,
                        "email": email,
                        "status": "prod",
                        "cv_processed": True,
                        "processed_timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                }

                # Send the HTTP request (webhook)
                candidate_email = "zubairmohideen95@gmail.com"
                send_webhook(payload, candidate_email)

                 # Schedule follow-up email for the actual applicant
                schedule_follow_up(email)  # Use the applicant's email

                return redirect(url_for('success'))  # Redirect to success page
            else:
                return "Failed to upload CV to S3."
        else:
            return 'Invalid file type. Please upload a PDF or DOCX file.'

    return render_template('index.html')

@app.route('/success')
def success():
    return render_template('success.html')

if __name__ == '__main__':
    app.run(debug=True)
