# ResumeFit

## Overview

Resume Fit Analyzer is an AI-powered application that evaluates how well a resume matches a job description. The platform analyzes candidate resumes, identifies missing skills, highlights strengths, and generates personalized recommendations to improve resume relevance for specific roles.

The application is designed to help job seekers optimize their resumes before applying, increasing the likelihood of passing Applicant Tracking Systems (ATS) and recruiter screening.

---

## Problem Statement

Many candidates submit the same resume for every job application, resulting in low ATS scores and missed opportunities.

This project addresses that challenge by automatically comparing resumes with job descriptions and providing actionable recommendations for tailoring resumes to specific roles.

---

## Key Features

### Resume Upload

* Upload resumes in PDF format
* Extract resume content automatically

### Job Description Analysis

* Compare resume against a target job description
* Identify relevant skills and keywords

### Skill Gap Detection

* Highlight missing technical and soft skills
* Identify areas for improvement

### AI-Powered Recommendations

* Generate personalized suggestions for improving resume relevance
* Recommend additional keywords and skills
* Improve ATS compatibility

---

## Technology Stack

* Python
* Streamlit
* Generative AI
* PDF Processing
* Natural Language Processing

---

## Project Structure

* app.py – Main application
* requirements.txt – Project dependencies
* sample_resume.pdf – Example resume
* sample_job_description.txt – Example job description

---

## Home Page

![Home](screenshots/home_page.png)

---

## Resume Upload

![Resume Upload](screenshots/upload_resume.png)

---

## Analysis Results

![Analysis Results](screenshots/analysis_results.png)

---

## Skill Gap Analysis

![Skill Gap Analysis](screenshots/skill_gap_analysis.png)

---

## AI Recommendations

![Recommendations](screenshots/recommendations.png)

---

## Future Enhancements

* ATS score prediction
* Resume rewriting assistance
* Cover letter generation
* Multi-role resume optimization
* Interview preparation suggestions
* LinkedIn profile analysis

---

## Repository Note

API credentials and sensitive configuration files have been excluded from the public repository. Configure required environment variables using the `.env.example` file.
