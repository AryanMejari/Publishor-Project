from email.mime.application import MIMEApplication
from flask import (
    Flask,
    request,
    jsonify,
    send_file,
    send_from_directory,
    render_template,
    redirect,
    url_for,
    session,
)
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO
import os
from datetime import datetime
from email.utils import formataddr
import threading
import random
import re
import secrets
import tempfile
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash
from basic_paper_evaluator import paper_evaluation_process
from academic_review import run_review
from firebase_store import (
    create_paper,
    get_paper_by_credentials,
    get_paper_by_id,
    get_community_user,
    get_community_user_by_email,
    create_community_user,
    update_community_user,
    create_challenge,
    get_challenge,
    update_challenge,
    list_challenges_for_paper,
    list_public_papers,
    create_reputation_event,
    initialize_firebase,
    update_paper,
)
from firebase_storage import download_bytes, upload_bytes

app = Flask(__name__, template_folder='templates', static_folder='static')
load_dotenv()
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
REVIEW_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review_reports')

def generate_unique_paper_id(max_attempts=20):
    """Generate a unique paper ID by checking existing Firestore documents."""
    current_year = datetime.now().year

    for _ in range(max_attempts):
        random_num = random.randint(1000, 9999)
        paper_id = f"ISIRP-{current_year}-{random_num}"
        if not get_paper_by_id(paper_id):
            return paper_id

    raise RuntimeError("Could not generate a unique paper ID. Please retry.")

@app.route('/files/<filename>')
def download_file(filename):
    """Serve static files from the files directory"""
    return send_from_directory('static/files', filename)

# Vercel only permits writes under the temporary system directory. Uploaded
# manuscripts are sent to Cloudinary; this folder is only a local fallback.
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'isirp-uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
# Email configuration
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')

def _email_shell(preheader, title, content):
    return f"""
    <html><body style="margin:0;background:#eef2ff;font-family:Arial,sans-serif;color:#172033;">
      <div style="padding:32px 12px;">
        <div style="max-width:620px;margin:auto;background:#ffffff;border-radius:18px;overflow:hidden;box-shadow:0 12px 32px rgba(15,23,42,.12);">
          <div style="padding:28px 32px;background:linear-gradient(135deg,#172554,#4338ca);color:#fff;">
            <div style="font-size:13px;letter-spacing:2px;font-weight:bold;">ISIRP RESEARCH PUBLICATIONS</div>
            <h1 style="margin:14px 0 4px;font-size:26px;">{title}</h1>
            <div style="font-size:13px;color:#c7d2fe;">{preheader}</div>
          </div>
          <div style="padding:32px;">{content}</div>
          <div style="padding:20px 32px;background:#f8fafc;color:#64748b;font-size:12px;">
            Automated platform notification · ISIRP hobby research platform<br>
            This message is not accredited peer-review certification.
          </div>
        </div>
      </div>
    </body></html>
    """


def _send_message(to_email, subject, html_body, attachments=None):
    msg = MIMEMultipart()
    msg["From"] = formataddr(("ISIRP Research Publications", EMAIL_ADDRESS))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    for path in attachments or []:
        with open(path, "rb") as file:
            part = MIMEApplication(file.read(), Name=os.path.basename(path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(path)}"'
        msg.attach(part)
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def send_confirmation_email(to_email, paper_id, submission_date):
    content = f"""
      <p style="font-size:16px;">Dear Researcher,</p>
      <p>Your manuscript has been received successfully and is now queued for automated screening.</p>
      <div style="padding:20px;background:#eef2ff;border-radius:12px;margin:24px 0;">
        <div style="font-size:12px;color:#4f46e5;font-weight:bold;">SUBMISSION REFERENCE</div>
        <div style="font-size:24px;font-weight:bold;margin-top:8px;">{paper_id}</div>
        <div style="color:#475569;margin-top:6px;">Submitted on {submission_date}</div>
      </div>
      <p>Keep this Paper ID and your corresponding-author email to access the dashboard and track your review.</p>
      <p style="color:#475569;">Next: manuscript screening, review report generation, and certificate processing.</p>
      <p>Regards,<br><strong>ISIRP Editorial Team</strong></p>
    """
    return _send_message(
        to_email,
        f"Submission received · {paper_id}",
        _email_shell("Your manuscript is safely registered", "Submission received", content),
    )


def send_review_report_email(to_email, paper_id, report_path):
    content = f"""
      <p style="font-size:16px;">Your automated screening report for <strong>{paper_id}</strong> is ready.</p>
      <div style="padding:18px;background:#f0fdf4;border-left:4px solid #16a34a;border-radius:8px;margin:22px 0;">
        <strong>Review stage complete</strong><br>
        The report is attached to this email. It contains structural checks, scores,
        findings, counter-arguments, and suggested future directions.
      </div>
      <p>You can also use your Paper ID and corresponding-author email to access the dashboard.</p>
      <p style="color:#64748b;font-size:13px;">This is automated screening feedback, not accredited peer review.</p>
      <p>Regards,<br><strong>ISIRP Academic Screening Team</strong></p>
    """
    return _send_message(
        to_email,
        f"Screening report ready · {paper_id}",
        _email_shell("Your automated academic screening is complete", "Review report ready", content),
        [report_path],
    )
    
def send_certificates_email(to_email, paper_id, certificate_paths):
    content = f"""
      <p style="font-size:16px;">Congratulations. Your paper <strong>{paper_id}</strong> has completed the ISIRP publication workflow.</p>
      <div style="padding:20px;background:#fffbeb;border:1px solid #f59e0b;border-radius:12px;margin:22px 0;">
        <div style="font-size:12px;color:#92400e;font-weight:bold;">PUBLICATION STATUS</div>
        <div style="font-size:22px;font-weight:bold;color:#166534;margin-top:8px;">Accepted for publication</div>
        <div style="color:#475569;margin-top:6px;">Certificates for all listed authors are attached.</div>
      </div>
      <p>Download and distribute the attached certificates to your co-authors. Your verification page remains available through the Paper ID.</p>
      <p>Regards,<br><strong>ISIRP Editorial Team</strong></p>
    """
    return _send_message(
        to_email,
        f"Accepted for publication · certificates · {paper_id}",
        _email_shell("Publication workflow complete", "Publication certificate issued", content),
        certificate_paths,
    )

# Serve index.html as the main page
@app.route('/')
def home():
    return render_template('index.html')

# Serve submit.html
@app.route('/submit')
def submit_form():
    return render_template('submit.html')

# Existing routes for terms, privacy, etc.
@app.route('/termsandconditions')
def terms():
    return render_template('termsandconditions.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/disclaimer')
def disclaimer():
    return render_template('disclaimer.html')

@app.route('/return')
def return_policy():
    return render_template('return.html')

@app.route('/refund')
def refund():
    return render_template('refund.html')

@app.route('/faq')
def faq():
    return render_template('faq.html')

@app.route('/dashboard')
def dashboard():
    """Serve the dashboard template"""
    return render_template('dashboard.html')


@app.route('/community')
def community():
    """Serve the real-researcher challenge network."""
    return render_template('community.html')

@app.route('/submissionagreement')
def submissionagreement():
    """Serve the dashboard template"""
    return render_template('submissionagreement.html')

@app.route('/AIReviewprocess')
def AIReviewprocess():
    """Serve the dashboard template"""
    return render_template('AIReviewprocess.html')

@app.route('/submission-success')
def submission_success():
    """Display the successful submission confirmation."""
    return render_template('payment_success.html', paper_id=request.args.get('paper_id'))

@app.route('/prepare-submission', methods=['POST'])
def prepare_submission():
    """Save a submission and its manuscript without payment processing."""
    try:
        # Check if file part is present
        if 'manuscript' not in request.files:
             return jsonify({'success': False, 'error': 'No file part'}), 400
        
        file = request.files['manuscript']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
            
        # Get other form data
        # Note: In multipart/form-data, numbers are strings like '1', '2'
        paper_title = request.form.get('paper_title')
        corresponding_author_name = request.form.get('corresponding_author_name')
        corresponding_author_email = request.form.get('corresponding_author_email')
        
        # Validate required fields
        if not paper_title or not corresponding_author_name or not corresponding_author_email:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        # Keep the manuscript in Firebase Storage; the temporary file is only
        # used while the synchronous review pipeline runs.
        import werkzeug
        filename = werkzeug.utils.secure_filename(file.filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{timestamp}_{filename}"
        manuscript_bytes = file.read()
        if not manuscript_bytes:
            return jsonify({'success': False, 'error': 'The manuscript is empty'}), 400

        # Generate unique paper ID
        paper_id = generate_unique_paper_id()
        submission_date = datetime.now().strftime('%Y-%m-%d')
        manuscript_storage_path = upload_bytes(
            manuscript_bytes,
            f"manuscripts/{paper_id}/{filename}",
            "application/octet-stream",
        )

        create_paper(paper_id, {
            'paper_title': paper_title,
            'corresponding_author_name': corresponding_author_name,
            'corresponding_author_email': corresponding_author_email,
            'additional_author_name_1': request.form.get('additional_author_name_1'),
            'additional_author_name_2': request.form.get('additional_author_name_2'),
            'additional_author_name_3': request.form.get('additional_author_name_3'),
            'additional_author_name_4': request.form.get('additional_author_name_4'),
            'additional_author_name_5': request.form.get('additional_author_name_5'),
            'corresponding_author_certificate': None,
            'additional_author_cert_1': None,
            'additional_author_cert_2': None,
            'additional_author_cert_3': None,
            'additional_author_cert_4': None,
            'additional_author_cert_5': None,
            'submission_date': submission_date,
            'manuscript_filename': filename,
            'manuscript_storage_path': manuscript_storage_path,
            'payment_status': 'not_required',
            'review_status': 'queued',
        })

        suffix = os.path.splitext(filename)[1].lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_file.write(manuscript_bytes)
            temporary_path = temporary_file.name
        try:
            report_path = run_review(paper_id, temporary_path)
            send_review_report_email(
                corresponding_author_email, paper_id, report_path
            )
            from certificate_generator import generate_certificates

            generate_certificates(paper_id)
            send_confirmation_email(
                corresponding_author_email, paper_id, submission_date
            )
        finally:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
        
        return jsonify({'success': True, 'paper_id': paper_id})
        
    except Exception as e:
        print(f"Error in prepare_submission: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Serve static files (CSS, JS, images)
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)

# Login API
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    paper_id = data.get('paper_id')
    email = data.get('email')
    
    # Validate input
    if not paper_id or not email:
        return jsonify({'success': False, 'error': 'Missing paper ID or email'}), 400
    
    # Query Firestore
    paper = get_paper_by_credentials(paper_id, email)
    
    if paper:
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

# Dashboard data API
@app.route('/api/dashboard-data', methods=['POST'])
def api_dashboard_data():
    data = request.json
    paper_id = data.get('paper_id')
    email = data.get('email')
    
    paper = get_paper_by_credentials(paper_id, email)
    
    if not paper:
        return jsonify({'success': False, 'error': 'Paper not found'}), 404
    
    # Convert paper data to dictionary
    paper_data = {
        'paper_title': paper.get('paper_title'),
        'corresponding_author_name': paper.get('corresponding_author_name'),
        'corresponding_author_email': paper.get('corresponding_author_email'),
        'paper_id': paper.get('paper_id'),
        'submission_date': paper.get('submission_date'),
        'corresponding_author_certificate': paper.get('corresponding_author_certificate'),
        'additional_author_name_1': paper.get('additional_author_name_1'),
        'additional_author_name_2': paper.get('additional_author_name_2'),
        'additional_author_name_3': paper.get('additional_author_name_3'),
        'additional_author_name_4': paper.get('additional_author_name_4'),
        'additional_author_name_5': paper.get('additional_author_name_5'),
        'additional_author_cert_1': paper.get('additional_author_cert_1'),
        'additional_author_cert_2': paper.get('additional_author_cert_2'),
        'additional_author_cert_3': paper.get('additional_author_cert_3'),
        'additional_author_cert_4': paper.get('additional_author_cert_4'),
        'additional_author_cert_5': paper.get('additional_author_cert_5'),
        'payment_status': paper.get('payment_status'),
        'review_status': paper.get('review_status', 'not_started'),
        'review_started_at': paper.get('review_started_at'),
        'review_completed_at': paper.get('review_completed_at'),
        'review_scores': paper.get('review_scores', {}),
        'review_findings': paper.get('review_findings', []),
        'review_section_checks': paper.get('review_section_checks', {}),
        'review_report_sha256': paper.get('review_report_sha256'),
        'review_disclosure': paper.get('review_disclosure'),
        'paper_sha256': paper.get('paper_sha256'),
        'certificate_sha256': paper.get('certificate_sha256'),
        'review_counter_arguments': paper.get('review_counter_arguments', []),
        'review_future_directions': paper.get('review_future_directions', []),
        'review_ai': paper.get('review_ai', {}),
        'review_ai_status': paper.get('review_ai_status', 'not_configured'),
    }
    
    return jsonify({'success': True, 'paper': paper_data})


def _community_user_from_session():
    user_id = session.get("community_user_id")
    return get_community_user(user_id) if user_id else None


def _is_community_moderator(user):
    moderator_emails = {
        email.strip().lower()
        for email in os.getenv("COMMUNITY_MODERATOR_EMAILS", "").split(",")
        if email.strip()
    }
    return bool(user and user.get("email", "").lower() in moderator_emails)


def _clean_text(value, field_name, minimum=1, maximum=2000):
    value = (value or "").strip()
    if len(value) < minimum or len(value) > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum} characters")
    return value


def _public_challenge(challenge, can_respond=False):
    return {
        "challenge_id": challenge.get("challenge_id"),
        "paper_id": challenge.get("paper_id"),
        "title": challenge.get("title"),
        "challenge_type": challenge.get("challenge_type"),
        "claim": challenge.get("claim"),
        "evidence": challenge.get("evidence"),
        "status": challenge.get("status"),
        "author_name": challenge.get("author_name"),
        "created_at": challenge.get("created_at"),
        "helpful_votes": challenge.get("helpful_votes", 0),
        "responses": challenge.get("responses", []),
        "can_respond": can_respond,
    }


@app.route('/api/community/register', methods=['POST'])
def community_register():
    data = request.get_json(silent=True) or {}
    try:
        display_name = _clean_text(data.get("display_name"), "Display name", 2, 80)
        email = _clean_text(data.get("email"), "Email", 5, 254).lower()
        password = _clean_text(data.get("password"), "Password", 8, 128)
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    if get_community_user_by_email(email):
        return jsonify({"success": False, "error": "An account already exists for this email"}), 409

    user_id = secrets.token_urlsafe(16)
    create_community_user(
        user_id,
        {
            "display_name": display_name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "reputation": 0,
            "badges": [],
            "created_at": datetime.utcnow().isoformat(),
        },
    )
    session["community_user_id"] = user_id
    return jsonify({"success": True, "user": {"user_id": user_id, "display_name": display_name, "reputation": 0, "badges": []}})


@app.route('/api/community/login', methods=['POST'])
def community_login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = get_community_user_by_email(email)
    if not user or not check_password_hash(user.get("password_hash", ""), password):
        return jsonify({"success": False, "error": "Invalid community credentials"}), 401
    session["community_user_id"] = user["user_id"]
    return jsonify(
        {
            "success": True,
            "user": {
                "user_id": user["user_id"],
                "display_name": user.get("display_name"),
                "reputation": user.get("reputation", 0),
                "badges": user.get("badges", []),
            },
        }
    )


@app.route('/api/community/logout', methods=['POST'])
def community_logout():
    session.pop("community_user_id", None)
    return jsonify({"success": True})


@app.route('/api/community/me')
def community_me():
    user = _community_user_from_session()
    if not user:
        return jsonify({"authenticated": False})
    return jsonify(
        {
            "authenticated": True,
            "user": {
                "user_id": user["user_id"],
                "display_name": user.get("display_name"),
                "reputation": user.get("reputation", 0),
                "badges": user.get("badges", []),
            },
        }
    )


@app.route('/api/community/papers')
def community_papers():
    return jsonify({"success": True, "papers": list_public_papers()})


@app.route('/api/community/papers/<paper_id>/challenges')
def community_paper_challenges(paper_id):
    if not get_paper_by_id(paper_id):
        return jsonify({"success": False, "error": "Paper not found"}), 404
    current_user = _community_user_from_session()
    paper = get_paper_by_id(paper_id)
    is_author = bool(
        current_user
        and current_user.get("email") == (paper.get("corresponding_author_email") or "").lower()
    )
    challenges = list_challenges_for_paper(paper_id)
    visible = [
        _public_challenge(
            challenge,
            can_respond=is_author and challenge.get("status") != "rejected",
        )
        for challenge in challenges
        if challenge.get("status") == "approved" or challenge.get("challenger_id") == (current_user or {}).get("user_id") or is_author
    ]
    return jsonify({"success": True, "challenges": visible})


@app.route('/api/community/challenges', methods=['POST'])
def community_create_challenge():
    user = _community_user_from_session()
    if not user:
        return jsonify({"success": False, "error": "Sign in to challenge a paper"}), 401
    data = request.get_json(silent=True) or {}
    paper_id = (data.get("paper_id") or "").strip()
    paper = get_paper_by_id(paper_id)
    if not paper:
        return jsonify({"success": False, "error": "Paper not found"}), 404
    if user.get("email") == (paper.get("corresponding_author_email") or "").strip().lower():
        return jsonify({"success": False, "error": "Use the author response flow for your own paper"}), 400
    try:
        challenge_type = _clean_text(data.get("challenge_type"), "Challenge type", 3, 40)
        title = _clean_text(data.get("title"), "Title", 5, 140)
        claim = _clean_text(data.get("claim"), "Claim", 20, 2000)
        evidence = _clean_text(data.get("evidence"), "Evidence", 20, 4000)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    challenge_id = secrets.token_urlsafe(16)
    create_challenge(
        challenge_id,
        {
            "paper_id": paper_id,
            "challenger_id": user["user_id"],
            "author_name": user.get("display_name"),
            "challenge_type": challenge_type,
            "title": title,
            "claim": claim,
            "evidence": evidence,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "helpful_votes": 0,
            "responses": [],
        },
    )
    return jsonify(
        {
            "success": True,
            "message": "Challenge submitted for author and moderator review",
            "challenge_id": challenge_id,
        }
    ), 201


@app.route('/api/community/challenges/<challenge_id>/respond', methods=['POST'])
def community_respond_to_challenge(challenge_id):
    user = _community_user_from_session()
    challenge = get_challenge(challenge_id)
    if not user or not challenge:
        return jsonify({"success": False, "error": "Challenge or session not found"}), 404
    paper = get_paper_by_id(challenge.get("paper_id"))
    if not paper or user.get("email") != (paper.get("corresponding_author_email") or "").strip().lower():
        return jsonify({"success": False, "error": "Only the paper author can respond"}), 403
    try:
        response_text = _clean_text((request.get_json(silent=True) or {}).get("response"), "Response", 10, 4000)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    responses = challenge.get("responses", [])
    responses.append(
        {
            "author_name": user.get("display_name"),
            "response": response_text,
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    update_challenge(challenge_id, {"responses": responses, "status": "answered"})
    return jsonify({"success": True})


@app.route('/api/community/challenges/<challenge_id>/vote', methods=['POST'])
def community_vote_challenge(challenge_id):
    user = _community_user_from_session()
    challenge = get_challenge(challenge_id)
    if not user or not challenge:
        return jsonify({"success": False, "error": "Challenge or session not found"}), 404
    if challenge.get("status") not in {"approved", "answered"}:
        return jsonify({"success": False, "error": "Only approved challenges can receive votes"}), 400
    voter_ids = challenge.get("voter_ids", [])
    if user["user_id"] in voter_ids:
        return jsonify({"success": False, "error": "You already marked this challenge helpful"}), 409
    voter_ids.append(user["user_id"])
    update_challenge(
        challenge_id,
        {"voter_ids": voter_ids, "helpful_votes": len(voter_ids)},
    )
    challenger = get_community_user(challenge.get("challenger_id"))
    if challenger:
        new_reputation = challenger.get("reputation", 0) + 2
        badges = list(challenger.get("badges", []))
        if new_reputation >= 10 and "Evidence Builder" not in badges:
            badges.append("Evidence Builder")
        update_community_user(
            challenger["user_id"],
            {"reputation": new_reputation, "badges": badges},
        )
        create_reputation_event(
            secrets.token_urlsafe(16),
            {
                "user_id": challenger["user_id"],
                "points": 2,
                "reason": "A researcher marked a challenge helpful",
                "challenge_id": challenge_id,
                "created_at": datetime.utcnow().isoformat(),
            },
        )
    return jsonify({"success": True, "helpful_votes": len(voter_ids)})


@app.route('/api/community/challenges/<challenge_id>/moderate', methods=['POST'])
def community_moderate_challenge(challenge_id):
    user = _community_user_from_session()
    challenge = get_challenge(challenge_id)
    if not _is_community_moderator(user):
        return jsonify({"success": False, "error": "Moderator access is required"}), 403
    if not challenge:
        return jsonify({"success": False, "error": "Challenge not found"}), 404
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in {"approved", "rejected"}:
        return jsonify({"success": False, "error": "Status must be approved or rejected"}), 400
    update_challenge(
        challenge_id,
        {
            "status": status,
            "moderated_by": user.get("user_id"),
            "moderated_at": datetime.utcnow().isoformat(),
        },
    )
    return jsonify({"success": True, "status": status})


@app.route('/download-review-report')
def download_review_report():
    paper_id = request.args.get('paper_id')
    email = request.args.get('email')
    if not paper_id or not email:
        return jsonify({'error': 'Paper ID and email are required'}), 400

    paper = get_paper_by_credentials(paper_id, email)
    if not paper or not paper.get('review_report_path'):
        return jsonify({'error': 'Review report is not available'}), 404

    report_path = paper["review_report_path"]
    report_bytes = download_bytes(report_path)
    if report_bytes is None and os.path.isfile(report_path):
        return send_file(report_path, as_attachment=True)
    if report_bytes is None:
        return jsonify({'error': 'Review report file not found'}), 404
    return send_file(
        BytesIO(report_bytes),
        as_attachment=True,
        download_name=f"{paper_id}_screening_report.pdf",
        mimetype="application/pdf",
    )


@app.route('/verify/<paper_id>')
def verify_paper(paper_id):
    paper = get_paper_by_id(paper_id)
    if not paper:
        return render_template('verify.html', paper=None, paper_id=paper_id), 404
    return render_template('verify.html', paper={
        'paper_id': paper.get('paper_id', paper_id),
        'paper_title': paper.get('paper_title'),
        'corresponding_author_name': paper.get('corresponding_author_name'),
        'submission_date': paper.get('submission_date'),
        'review_status': paper.get('review_status', 'not_started'),
        'review_completed_at': paper.get('review_completed_at'),
        'paper_sha256': paper.get('paper_sha256'),
        'certificate_sha256': paper.get('certificate_sha256'),
        'review_disclosure': paper.get('review_disclosure'),
    })

# Certificate download endpoint
@app.route('/download-certificate')
def download_certificate():
    path = request.args.get('path')
    if not path:
        return jsonify({'error': 'Missing path parameter'}), 400

    certificate_bytes = download_bytes(path)
    if certificate_bytes is None and os.path.isfile(path):
        return send_file(path, as_attachment=True)
    if certificate_bytes is None:
        return jsonify({'error': 'File not found'}), 404
    is_docx = path.lower().endswith(".docx")
    filename = os.path.basename(path)
    if not filename.lower().endswith((".pdf", ".docx")):
        filename = f"publication_certificate.{'docx' if is_docx else 'pdf'}"
    return send_file(
        BytesIO(certificate_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if is_docx
            else "application/pdf"
        ),
    )

# @app.route('/papers')
# def show_papers():
#     search_query = request.args.get('q', '')
#     
#     if search_query:
#         papers = ResearchPaper.query.filter(
#             (ResearchPaper.paper_title.ilike(f'%{search_query}%')) |
#             (ResearchPaper.corresponding_author_name.ilike(f'%{search_query}%')) |
#             (ResearchPaper.additional_author_name_1.ilike(f'%{search_query}%')) |
#             (ResearchPaper.additional_author_name_2.ilike(f'%{search_query}%')) |
#             (ResearchPaper.additional_author_name_3.ilike(f'%{search_query}%')) |
#             (ResearchPaper.additional_author_name_4.ilike(f'%{search_query}%')) |
#             (ResearchPaper.additional_author_name_5.ilike(f'%{search_query}%'))
#         ).all()
#     else:
#         papers = ResearchPaper.query.all()
#     
#     paper_list = []
#     for paper in papers:
#         description = f"Research paper titled '{paper.paper_title[:70]}{'...' if len(paper.paper_title) > 70 else ''}'"
#         
#         paper_list.append({
#             'title': paper.paper_title,
#             'corresponding_author': paper.corresponding_author_name,
#             'paper_id': paper.paper_id,
#             'submission_date': paper.submission_date,
#             'description': description
#         })
#     
#     return render_template('papers.html', papers=paper_list, search_query=search_query)

@app.route('/view-paper/<paper_id>')
def view_paper(paper_id):
    paper = get_paper_by_id(paper_id)
    
    if not paper:
        return "Paper not found", 404
    manuscript = download_bytes(paper.get("manuscript_storage_path"))
    if manuscript is None:
        return "File not found", 404
    extension = os.path.splitext(paper.get("manuscript_filename", ""))[1].lower()
    mimetype = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(extension, "application/octet-stream")
    return send_file(
        BytesIO(manuscript),
        as_attachment=False,
        download_name=paper.get("manuscript_filename", "manuscript"),
        mimetype=mimetype,
    )


@app.route('/AboutThisProject')
def about_project():
    return render_template('AboutThisProject.html')

def generate_certificates_background(paper_id):
    # This will run in a separate thread
    from certificate_generator import generate_certificates
    generate_certificates(paper_id)

if __name__ == '__main__':
    initialize_firebase()
    print("Firebase initialized successfully")
    port = int(os.getenv("PORT", "5000"))
    print(f"Server starting on http://127.0.0.1:{port}")
    
    app.run(debug=os.getenv("FLASK_DEBUG", "").lower() == "true", host="0.0.0.0", port=port)