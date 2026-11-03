# basic_paper_evaluator.py
import os
import re
import threading
import logging
import random
from datetime import datetime


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_text_from_file(file_path):
    """Extract text from PDF or DOCX files"""
    text = ""
    
    try:
        if file_path.lower().endswith('.pdf'):
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            for page in doc:
                text += page.get_text()
            doc.close()
            
        elif file_path.lower().endswith('.docx'):
            from docx import Document
            doc = Document(file_path)
            for paragraph in doc.paragraphs:
                text += paragraph.text + "\n"
                
    except Exception as e:
        logger.error("Error extracting text from file: " + str(e))
    
    return text

def calculate_readability_score(text):
    """Calculate Flesch Reading Ease score"""
    sentences = re.split(r'[.!?]+', text)
    words = re.findall(r'\w+', text.lower())
    
    if len(sentences) == 0 or len(words) == 0:
        return 0
    
    avg_sentence_length = len(words) / len(sentences)
    syllables = sum([count_syllables(word) for word in words])
    avg_syllables_per_word = syllables / len(words)
    
    # Flesch Reading Ease formula
    readability = 206.835 - (1.015 * avg_sentence_length) - (84.6 * avg_syllables_per_word)
    return max(0, min(100, readability))

def count_syllables(word):
    """Approximate syllable count for a word"""
    word = word.lower()
    if len(word) <= 3:
        return 1
    
    vowels = "aeiouy"
    count = 0
    prev_char_vowel = False
    
    for char in word:
        if char in vowels and not prev_char_vowel:
            count += 1
            prev_char_vowel = True
        else:
            prev_char_vowel = False
    
    # Adjust for words ending with 'e'
    if word.endswith('e'):
        count -= 1
    
    return max(1, count)

def analyze_academic_structure(paper_text):
    """Comprehensive analysis of academic structure"""
    text_lower = paper_text.lower()
    
    structure_analysis = {
        'abstract': {
            'present': 'abstract' in text_lower[:1000],
            'position': text_lower.find('abstract'),
            'quality': 0
        },
        'introduction': {
            'present': any(keyword in text_lower for keyword in ['introduction', 'introductory']),
            'position': min([text_lower.find(kw) for kw in ['introduction', 'introductory'] if text_lower.find(kw) != -1] or [999999]),
            'quality': 0
        },
        'literature_review': {
            'present': any(keyword in text_lower for keyword in ['literature review', 'previous research', 'related work']),
            'quality': 0
        },
        'methodology': {
            'present': any(keyword in text_lower for keyword in ['methodology', 'methods', 'research design', 'experimental setup']),
            'quality': 0
        },
        'results': {
            'present': any(keyword in text_lower for keyword in ['results', 'findings', 'data analysis', 'experimental results']),
            'quality': 0
        },
        'discussion': {
            'present': any(keyword in text_lower for keyword in ['discussion', 'analysis', 'interpretation']),
            'quality': 0
        },
        'conclusion': {
            'present': any(keyword in text_lower for keyword in ['conclusion', 'concluding remarks', 'summary']),
            'quality': 0
        },
        'references': {
            'present': any(keyword in text_lower for keyword in ['references', 'bibliography', 'works cited']),
            'quality': 0
        }
    }
    
    return structure_analysis

def generate_impressive_evaluation(paper_text, paper_id):
    """Generate a high-class, convincing paper evaluation"""
    if not paper_text or len(paper_text.strip()) < 200:
        return "The submitted manuscript contains insufficient content for a comprehensive evaluation. We recommend expanding the research material to meet academic standards."
    
    # Comprehensive analysis
    word_count = len(paper_text.split())
    sentence_count = len(re.split(r'[.!?]+', paper_text))
    paragraph_count = len([p for p in paper_text.split('\n\n') if p.strip()])
    readability_score = calculate_readability_score(paper_text)
    structure_analysis = analyze_academic_structure(paper_text)
    
    # Calculate structure quality score
    structure_score = sum(1 for section in structure_analysis.values() if section['present'])
    total_sections = len(structure_analysis)
    structure_percentage = (structure_score / total_sections) * 100
    
    # Vocabulary analysis
    words = re.findall(r'\w+', paper_text.lower())
    unique_words = set(words)
    lexical_diversity = len(unique_words) / len(words) if words else 0
    
    # Academic keyword detection
    academic_keywords = ['hypothesis', 'methodology', 'analysis', 'results', 'discussion', 
                        'conclusion', 'references', 'citation', 'theory', 'framework',
                        'empirical', 'quantitative', 'qualitative', 'experiment', 'study']
    academic_keyword_count = sum(1 for word in academic_keywords if word in paper_text.lower())
    
    # Generate evaluation
    evaluation_parts = []
    
    # Header
    evaluation_parts.append("📊 **COMPREHENSIVE RESEARCH PAPER EVALUATION**")
    evaluation_parts.append("=" * 60)
    evaluation_parts.append(f"Evaluation Date: {datetime.now().strftime('%B %d, %Y')}")
    evaluation_parts.append(f"Paper ID: {paper_id}")
    evaluation_parts.append("")
    
    # Executive Summary
    evaluation_parts.append("🎯 **EXECUTIVE SUMMARY**")
    evaluation_parts.append("-" * 40)
    
    if structure_percentage >= 80:
        evaluation_parts.append("This research paper demonstrates exceptional academic rigor and comprehensive structure. The manuscript exhibits strong potential for contribution to its field.")
    elif structure_percentage >= 60:
        evaluation_parts.append("The paper presents a solid research foundation with good structural organization. Several key academic components are well-developed.")
    elif structure_percentage >= 40:
        evaluation_parts.append("The manuscript shows promise but requires further development in academic structure and content organization.")
    else:
        evaluation_parts.append("The submission would benefit significantly from enhanced academic structure and more comprehensive content development.")
    
    evaluation_parts.append("")
    
    # Quantitative Analysis
    evaluation_parts.append("📈 **QUANTITATIVE ANALYSIS**")
    evaluation_parts.append("-" * 40)
    evaluation_parts.append(f"• Word Count: {word_count:,} words")
    evaluation_parts.append(f"• Sentence Count: {sentence_count} sentences")
    evaluation_parts.append(f"• Paragraph Count: {paragraph_count} paragraphs")
    evaluation_parts.append(f"• Lexical Diversity: {lexical_diversity:.2%} (unique word ratio)")
    evaluation_parts.append(f"• Readability Score: {readability_score:.1f}/100 (Flesch Reading Ease)")
    evaluation_parts.append(f"• Academic Keywords: {academic_keyword_count} detected")
    evaluation_parts.append("")
    
    # Structural Assessment
    evaluation_parts.append("🏗️ **STRUCTURAL ASSESSMENT**")
    evaluation_parts.append("-" * 40)
    evaluation_parts.append(f"Overall Structure Score: {structure_percentage:.1f}%")
    evaluation_parts.append("")
    
    for section, data in structure_analysis.items():
        status = "✅ PRESENT" if data['present'] else "❌ ABSENT"
        section_name = section.replace('_', ' ').title()
        evaluation_parts.append(f"• {section_name}: {status}")
    
    evaluation_parts.append("")
    
    # Detailed Analysis
    evaluation_parts.append("🔍 **DETAILED ANALYSIS**")
    evaluation_parts.append("-" * 40)
    
    # Word count analysis
    if word_count > 8000:
        evaluation_parts.append("• The manuscript demonstrates extensive research depth with comprehensive coverage of the subject matter.")
    elif word_count > 5000:
        evaluation_parts.append("• The paper provides substantial content with thorough analysis and discussion.")
    elif word_count > 3000:
        evaluation_parts.append("• The research presents adequate content for academic consideration.")
    else:
        evaluation_parts.append("• The content would benefit from further elaboration and detailed analysis.")
    
    # Readability analysis
    if readability_score > 70:
        evaluation_parts.append("• Excellent readability: The writing is clear and accessible to a broad academic audience.")
    elif readability_score > 50:
        evaluation_parts.append("• Good readability: The text is generally clear with appropriate academic tone.")
    else:
        evaluation_parts.append("• The writing style could be enhanced for better academic communication.")
    
    # Lexical diversity analysis
    if lexical_diversity > 0.6:
        evaluation_parts.append("• Exceptional vocabulary range: Demonstrates sophisticated academic language use.")
    elif lexical_diversity > 0.45:
        evaluation_parts.append("• Good vocabulary diversity: Appropriate academic terminology employed effectively.")
    else:
        evaluation_parts.append("• Vocabulary could be enriched with more varied academic terminology.")
    
    evaluation_parts.append("")
    
    # Recommendations
    evaluation_parts.append("💡 **RECOMMENDATIONS FOR ENHANCEMENT**")
    evaluation_parts.append("-" * 40)
    
    recommendations = []
    if not structure_analysis['abstract']['present']:
        recommendations.append("Consider adding a concise abstract summarizing the research objectives and findings")
    if not structure_analysis['literature_review']['present']:
        recommendations.append("Include a literature review section to contextualize your research within existing scholarship")
    if not structure_analysis['methodology']['present']:
        recommendations.append("Elaborate on the research methodology with detailed procedures and analytical approaches")
    if not structure_analysis['references']['present']:
        recommendations.append("Ensure proper citation format and comprehensive reference list")
    
    if readability_score < 50:
        recommendations.append("Improve sentence structure for enhanced readability and academic flow")
    if lexical_diversity < 0.4:
        recommendations.append("Enrich vocabulary with discipline-specific terminology and varied expression")
    
    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            evaluation_parts.append(f"{i}. {rec}")
    else:
        evaluation_parts.append("The paper demonstrates strong academic standards. Continue maintaining this level of excellence.")
    
    evaluation_parts.append("")
    
    # Final Assessment
    evaluation_parts.append("⭐ **FINAL ASSESSMENT**")
    evaluation_parts.append("-" * 40)
    
    if structure_percentage >= 80 and word_count > 5000:
        final_assessments = [
            "This research paper exhibits outstanding academic quality with comprehensive coverage and excellent structure.",
            "The manuscript demonstrates exceptional research rigor and makes a valuable contribution to the field.",
            "A exemplary piece of academic work that meets the highest standards of scholarly communication."
        ]
    elif structure_percentage >= 60:
        final_assessments = [
            "This paper presents a solid research foundation with good potential for academic contribution.",
            "The manuscript shows strong research capabilities with well-developed academic components.",
            "A commendable research effort that demonstrates good understanding of academic standards."
        ]
    else:
        final_assessments = [
            "This submission shows research potential but requires further development to meet academic standards.",
            "The paper demonstrates initial research understanding but would benefit from enhanced academic structure.",
            "A promising start that with additional development could become a strong academic contribution."
        ]
    
    evaluation_parts.append(random.choice(final_assessments))
    evaluation_parts.append("")
    evaluation_parts.append("We commend your research efforts and look forward to your continued academic contributions.")
    
    return "\n".join(evaluation_parts)

def paper_evaluation_process(paper_id, paper_path, author_email):
    """Main process for evaluating a paper"""
    evaluation = None
    
    try:
        # Extract text from file
        paper_text = extract_text_from_file(paper_path)
        
        if paper_text and len(paper_text.strip()) > 200:
            # Evaluate paper with impressive evaluation
            evaluation = generate_impressive_evaluation(paper_text, paper_id)
        else:
            evaluation = "The submitted content is insufficient for comprehensive evaluation. Please ensure your research paper contains adequate academic content for proper assessment."
            
    except Exception as e:
        logger.error("Error in paper evaluation process: " + str(e))
        evaluation = "Our advanced evaluation system encountered a technical difficulty. Our editorial team will provide a manual assessment shortly."
    
    # Send evaluation email
    send_evaluation_email(author_email, paper_id, evaluation)

def send_evaluation_email(to_email, paper_id, evaluation):
    """Send impressive paper evaluation email"""
    from server import EMAIL_ADDRESS, EMAIL_PASSWORD, SMTP_SERVER, SMTP_PORT
    
    subject = '🌟 Premium Research Paper Evaluation Complete'
    
    # Convert plain text evaluation to HTML with styling
    html_evaluation = evaluation.replace('\n', '<br>')
    html_evaluation = html_evaluation.replace('📊 **', '<h2 style="color: #2563eb; margin-bottom: 10px;">')
    html_evaluation = html_evaluation.replace('🎯 **', '<h3 style="color: #059669; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('📈 **', '<h3 style="color: #dc2626; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('🏗️ **', '<h3 style="color: #7c3aed; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('🔍 **', '<h3 style="color: #ea580c; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('💡 **', '<h3 style="color: #db2777; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('⭐ **', '<h3 style="color: #ca8a04; margin-top: 20px; margin-bottom: 8px;">')
    html_evaluation = html_evaluation.replace('**', '</h3>')
    
    html_evaluation = html_evaluation.replace('✅', '<span style="color: #059669; font-weight: bold;">✓</span>')
    html_evaluation = html_evaluation.replace('❌', '<span style="color: #dc2626; font-weight: bold;">✗</span>')
    html_evaluation = html_evaluation.replace('•', '•')
    
    # Create email body
    body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }}
            .container {{
                background: white;
                border-radius: 15px;
                padding: 30px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                margin: 20px auto;
            }}
            .header {{
                text-align: center;
                padding: 20px;
                background: linear-gradient(135deg, #2563eb 0%, #1e40af 100%);
                color: white;
                border-radius: 10px;
                margin-bottom: 30px;
            }}
            .evaluation-content {{
                background: #f8fafc;
                padding: 25px;
                border-radius: 10px;
                border-left: 5px solid #2563eb;
                margin: 20px 0;
            }}
            .metric-box {{
                background: #e0f2fe;
                padding: 15px;
                border-radius: 8px;
                margin: 10px 0;
                border-left: 4px solid #0369a1;
            }}
            .recommendation {{
                background: #f0fdf4;
                padding: 15px;
                border-radius: 8px;
                margin: 10px 0;
                border-left: 4px solid #16a34a;
            }}
            .footer {{
                text-align: center;
                margin-top: 30px;
                padding: 20px;
                background: #f1f5f9;
                border-radius: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎓 ISIRP Research Evaluation</h1>
                <p>Comprehensive Academic Assessment Report</p>
            </div>
            
            <div class="evaluation-content">
                {html_evaluation}
            </div>
            
            <div class="footer">
                <p>This evaluation was generated by ISIRP's Advanced AI Assessment System</p>
                <p>📍 International Scientific of Innovative Research Publications</p>
                <p>📧 teamisirp@gmail.com | 🌐 www.isirp.tech</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Send email
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import smtplib
    from email.utils import formataddr
    
    msg = MIMEMultipart()
    msg["From"] = formataddr(("ISIRP Research Evaluation", EMAIL_ADDRESS))
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        logger.info(f"Premium evaluation email sent for paper {paper_id}")
    except Exception as e:
        logger.error("Failed to send evaluation email: " + str(e))