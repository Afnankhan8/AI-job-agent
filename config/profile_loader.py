"""
Candidate Profile Loader.

Reads config/user_profile.json and exposes the candidate's details
to the form-filling engine and the AI engine.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class PersonalInfo:
    first_name: str
    last_name: str
    full_name: str
    email: str
    phone: str
    location: str
    linkedin_url: str
    linkedin_email: str
    linkedin_password: str
    workday_email: str
    workday_password: str
    github_url: str
    portfolio_url: str
    resume_path: str          # absolute or relative path to the resume PDF


@dataclass
class WorkPreferences:
    desired_title: str
    expected_salary: str
    notice_period: str
    work_authorization: bool  # legally authorised to work in target country
    requires_sponsorship: bool
    years_of_experience: int


@dataclass
class Education:
    degree: str
    institution: str
    year: int


@dataclass
class Experience:
    title: str
    company: str
    duration: str
    highlights: List[str]


@dataclass
class CandidateProfile:
    personal: PersonalInfo
    work_preferences: WorkPreferences
    skills: List[str]
    education: List[Education]
    experience: List[Experience]
    cover_letter_template: str
    screening_answers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def load_from_file(cls, filepath: str) -> "CandidateProfile":
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(
                f"User profile not found at '{filepath}'.\n"
                "Please edit config/user_profile.json with your details."
            )

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

        p = data.get("personal", {})
        w = data.get("work_preferences", {})

        personal = PersonalInfo(
            first_name=p.get("first_name", ""),
            last_name=p.get("last_name", ""),
            full_name=p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            email=p.get("email", ""),
            phone=p.get("phone", ""),
            location=p.get("location", ""),
            linkedin_url=p.get("linkedin_url", ""),
            linkedin_email=p.get("linkedin_email") or p.get("email", ""),
            linkedin_password=p.get("linkedin_password", ""),
            workday_email=p.get("workday_email") or p.get("email", ""),
            workday_password=p.get("workday_password", ""),
            github_url=p.get("github_url", ""),
            portfolio_url=p.get("portfolio_url", ""),
            resume_path=p.get("resume_path", "resume.pdf"),
        )

        prefs = WorkPreferences(
            desired_title=w.get("desired_title", ""),
            expected_salary=w.get("expected_salary", "Negotiable"),
            notice_period=w.get("notice_period", "Immediately"),
            work_authorization=bool(w.get("work_authorization", True)),
            requires_sponsorship=bool(w.get("requires_sponsorship", False)),
            years_of_experience=int(w.get("years_of_experience", 3)),
        )

        # Parse skills list
        skills = data.get("skills", [])

        # Parse education list
        education = []
        for edu in data.get("education", []):
            education.append(Education(
                degree=edu.get("degree", ""),
                institution=edu.get("institution", ""),
                year=int(edu.get("year", 0)),
            ))

        # Parse experience list
        experience = []
        for exp in data.get("experience", []):
            experience.append(Experience(
                title=exp.get("title", ""),
                company=exp.get("company", ""),
                duration=exp.get("duration", ""),
                highlights=exp.get("highlights", []),
            ))

        return cls(
            personal=personal,
            work_preferences=prefs,
            skills=skills,
            education=education,
            experience=experience,
            cover_letter_template=data.get("cover_letter_template", ""),
            screening_answers=data.get("screening_answers", {}),
        )

    def to_summary(self) -> str:
        """
        Return a structured text summary of the candidate's profile.
        Used as context for AI prompts.
        """
        lines = [
            f"Name: {self.personal.full_name}",
            f"Location: {self.personal.location}",
            f"Desired Role: {self.work_preferences.desired_title}",
            f"Years of Experience: {self.work_preferences.years_of_experience}",
            f"Skills: {', '.join(self.skills)}",
        ]

        if self.education:
            lines.append("\nEducation:")
            for edu in self.education:
                lines.append(f"  - {edu.degree} from {edu.institution} ({edu.year})")

        if self.experience:
            lines.append("\nExperience:")
            for exp in self.experience:
                lines.append(f"  - {exp.title} at {exp.company} ({exp.duration})")
                for h in exp.highlights:
                    lines.append(f"    • {h}")

        return "\n".join(lines)
