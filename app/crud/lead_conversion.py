"""Read-only lead provenance for student profiles."""
from datetime import timezone

def conversion_info(row):
    if row is None:
        return None
    return {key: getattr(row, key) for key in (
        "lead_id", "student_id", "student_code", "full_name", "phone", "email", "source",
        "interest", "message", "actor_id", "actor_name", "email_status") } | {
        "created_at": row.created_at.replace(tzinfo=timezone.utc),
        "email_sent_at": row.email_sent_at.replace(tzinfo=timezone.utc) if row.email_sent_at else None,
    }
