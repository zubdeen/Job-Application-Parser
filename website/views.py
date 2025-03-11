from flask import Flask, request, render_template, redirect, url_for
import os
import boto3
from botocore.exceptions import NoCredentialsError
from werkzeug.utils import secure_filename
import PyPDF2  # For PDF text extraction
import pdfplumber
import re  # For parsing text
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import json
from datetime import datetime
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

# Initialize Flask app
app = Flask(__name__)

# Configure upload folder and allowed file extensions
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'docx'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# Ensure the upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# AWS S3 credentials
AWS_ACCESS_KEY = 'AKIA3ZQKNITNKWK5UO3P'
AWS_SECRET_KEY = '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'
S3_BUCKET_NAME = 'zubair-folder'

# SendGrid API key
SENDGRID_API_KEY = 'sendgrid-api-key'

# Helper function to check file extensions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Function to upload file to S3
def upload_to_s3(file_path, bucket_name, object_name):
    s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    try:
        s3.upload_file(file_path, bucket_name, object_name)
        print(f"Upload successful: {object_name}")
        return f"https://{bucket_name}.s3.amazonaws.com/{object_name}"
    except FileNotFoundError:
        print("The file was not found.")
        return None
    except NoCredentialsError:
        print("Credentials not available.")
        return None

# Function to extract text from PDF
def extract_text_from_pdf(pdf_path):
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ''
    return text

def extract_personal_info(text):
    # Extract name, email, and phone number
    name = re.search(r"Name:\s*(.*)", text, re.IGNORECASE)
    email = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)  # Matches email
    phone = re.search(r'(\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,4})', text)  # Matches phone numbers

    personal_info = {
        "name": name.group(0) if name else None,
        "email": email.group(0) if email else None,
        "phone": phone.group(0) if phone else None
    }
    return personal_info

# Function to send follow-up email
def send_follow_up_email(applicant_email):
    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
    from_email = Email("zubairmohideen95@gmail.com")
    to_email = To(applicant_email)  # Applicant's email
    subject = "Your CV is under review"
    content = Content("text/plain", "Thank you for submitting your CV. It is currently under review.")
    mail = Mail(from_email, to_email, subject, content)

    try:
        response = sg.client.mail.send.post(request_body=mail.get())
        print(f"Email sent to {applicant_email}. Status code: {response.status_code}")
    except Exception as e:
        print(f"Failed to send email: {e}")

def extract_education(text):
    # Look for keywords like "Education", "Degree", "University", etc.
    education_keywords = ["education", "degree", "university", "college", "school"]
    sentences = text.split('\n')  # Split text into lines
    education_section = []

    for sentence in sentences:
        if any(keyword in sentence.lower() for keyword in education_keywords):
            education_section.append(sentence.strip())
    return education_section

def extract_qualifications(text):
    # Look for keywords like "Skills", "Qualifications", "Certifications", etc.
    qualifications_keywords = ["skills", "qualifications", "certifications", "technical skills"]
    sentences = text.split('\n')
    qualifications_section = []

    for sentence in sentences:
        if any(keyword in sentence.lower() for keyword in qualifications_keywords):
            qualifications_section.append(sentence.strip())
    return qualifications_section

def extract_projects(text):
    # Look for keywords like "Projects", "Experience", "Work", etc.
    projects_keywords = ["projects", "experience", "work", "internship"]
    sentences = text.split('\n')
    projects_section = []

    for sentence in sentences:
        if any(keyword in sentence.lower() for keyword in projects_keywords):
            projects_section.append(sentence.strip())
    return projects_section


def save_to_google_sheets(data):
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)

        sheet_id = "1L_tHvB5PTru_d8CWyr3dDlsGvYNPXbMn9PsU1qv5m00"
        spreadsheet = client.open_by_key(sheet_id)
        print(f"Opened Spreadsheet: {spreadsheet.title}")

        worksheets = spreadsheet.worksheets()
        print("Available Sheets:", [ws.title for ws in worksheets])

            # Make sure you're using the correct sheet name
        sheet = spreadsheet.sheet1  # OR use: sheet = spreadsheet.worksheet("YourSheetName")

            # Debug: Check the data before appending
        print("Data being sent to Google Sheets:", data)
        print("Type of data:", type(data))

            # Append row
        sheet.append_row(data)
        print("Row added successfully!")
    except Exception as e:
        print(f"Error while adding row to Google Sheets: {e}")

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
            cv_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            cv.save(cv_path)

            # Upload the CV to S3 and get the public link
            cv_public_link = upload_to_s3(cv_path, S3_BUCKET_NAME, filename)
            if cv_public_link:
                print(f"CV uploaded successfully. Public link: {cv_public_link}")
                # Extract text from the CV
                cv_text = extract_text_from_pdf(cv_path)
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
                        "status": "testing",  # Change to "prod" for final submission
                        "cv_processed": True,
                        "processed_timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                }

                # Send the HTTP request (webhook)
                url = "https://rnd-assignment.automations-3d6.workers.dev/"
                headers = {
                    "X-Candidate-Email": "zubairmohideen95@gmail.com",
                    "Content-Type": "application/json"
                }
                response = requests.post(url, headers=headers, data=json.dumps(payload))
                print(f"Webhook response: {response.status_code}, {response.text}")

                # Send follow-up email
                send_follow_up_email(email)  # Use the applicant's email from the form

                return redirect(url_for('success'))  # Redirect to success page
            else:
                return "Failed to upload CV to S3."
        else:
            return 'Invalid file type. Please upload a PDF or DOCX file.'

    return render_template('index.html')

@app.route('/success')
def success():
    return 'Form submitted successfully!'

if __name__ == '__main__':
    app.run(debug=True)
