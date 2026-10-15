from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Per-user agent settings, for example {"match_scoring": {"model": "..."}}.
    agent_config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    jobs: Mapped[list["ScrapedJob"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    search_runs: Mapped[list["SearchRun"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    applications: Mapped[list["Application"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    browser_sessions: Mapped[list["BrowserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[User] = relationship(back_populates="search_runs")


class ScrapedJob(Base):
    __tablename__ = "scraped_jobs"
    __table_args__ = (UniqueConstraint("user_id", "source", "external_url", name="uq_user_job_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    external_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    company: Mapped[str | None] = mapped_column(String(500), nullable=True)
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_posted: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Lets the queue and Jobs screen distinguish a manual search from the ambient scheduler.
    fetched_by_cron: Mapped[bool] = mapped_column(default=False, index=True)
    search_run_id: Mapped[int | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"), nullable=True)
    ai_status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    application_status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True)
    resume_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    experience_match: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Scorer 2 (resume agent on): which resume folder fits this job, e.g. "backend-developer".
    resume_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="jobs")
    applications: Mapped[list["Application"]] = relationship(back_populates="job", cascade="all, delete-orphan", foreign_keys="Application.job_id")


class Application(Base):
    __tablename__ = "job_applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_user_application_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scraped_jobs.id", ondelete="CASCADE"), index=True)
    resume_key: Mapped[str] = mapped_column(Text)
    resume_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    stage: Mapped[str] = mapped_column(String(100), default="QUEUED")
    queue_position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_data: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AI usage of the apply agent for this application (all attempts).
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Resume agent decision: candidates + scores, chosen / generated resume, pinned by you, keywords added.
    resume_agent: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[ScrapedJob] = relationship(back_populates="applications", foreign_keys=[job_id])
    events: Mapped[list["ApplicationEvent"]] = relationship(back_populates="application", cascade="all, delete-orphan", passive_deletes=True)


class ApplicationEvent(Base):
    """Step-by-step log of one application run: stage changes, what the AI decided and why, actions, errors."""

    __tablename__ = "job_application_events"
    __table_args__ = (Index("job_application_events_app_attempt_idx", "application_id", "attempt", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    stage_key: Mapped[str] = mapped_column(String(20), default="open")  # open|login|find|fields|review|failed
    kind: Mapped[str] = mapped_column(String(20), default="info")  # stage|thinking|action|info|warning|error
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    application: Mapped[Application] = relationship(back_populates="events")


class BrowserSession(Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (UniqueConstraint("user_id", "source", name="uq_user_browser_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="NEEDS_LOGIN", index=True)
    auth_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    user: Mapped[User] = relationship(back_populates="browser_sessions")


class SystemPrompt(Base):
    __tablename__ = "system_prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Resume(Base):
    """Metadata for every resume object in storage (uploaded by you or generated by the resume agent)."""

    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("user_id", "storage_key", name="uq_user_resume_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100), index=True)  # resume folder, e.g. "backend-developer"
    label: Mapped[str] = mapped_column(String(200))  # "Backend Developer (original)", "Python-heavy backend"
    origin: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded | generated
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("scraped_jobs.id", ondelete="SET NULL"), nullable=True)
    upload_name: Mapped[str] = mapped_column(String(200))  # file name shown on LinkedIn / Indeed; unique per user
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # structured resume (generated ones)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)  # cached plain text for scoring
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResumePortalUpload(Base):
    """A resume that already sits in your LinkedIn / Indeed account under `display_name` — select it, don't re-upload."""

    __tablename__ = "resume_portal_uploads"
    __table_args__ = (UniqueConstraint("resume_id", "portal", name="uq_resume_portal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id", ondelete="CASCADE"))
    portal: Mapped[str] = mapped_column(String(50))  # linkedin | indeed
    display_name: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
