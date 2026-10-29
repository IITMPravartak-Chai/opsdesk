"""SQLAlchemy models (one module per functional area; the schema is created by the Alembic revisions)."""
from ..base import Base  # noqa: F401
from .channels import Channel, InboundMessage  # noqa: F401
from .cmdb import CIDependency, ConfigurationItem, Service  # noqa: F401
from .core import (Account, AuditLog, AuthBlacklistedToken, BusinessHours, ContactIdentity, Department, Team, TeamAccount,  # noqa: F401
                   TeamMember, Tenant, User)
from .misc import AIFeedback, KnowledgeArticle, Rule  # noqa: F401
from .tickets import (Approval, Attachment, CannedResponse, CSATSurvey, Interaction, Notification,  # noqa: F401
                      NotificationPreference, SavedQueue, ServiceCatalogItem, TimeEntry, Ticket, TicketEvent,
                      TicketLink, TicketSequence, TicketWatcher)
from .workflow import (Category, CustomField, Priority, PriorityMatrix, SLAPolicy, SLAPolicyTicketType,  # noqa: F401
                       SLATarget, State, TicketType, Transition, Workflow)


# --- Human readable labels (history entries, automation conditions, admin pickers) --------------------------
ENV_LABELS = {"prod": "Production", "staging": "Staging", "dev": "Development"}
CI_TYPE_LABELS = {
    "application": "Application", "ai_model": "AI / ML model", "llm_app": "LLM / GenAI application",
    "data_pipeline": "Data pipeline", "platform": "Platform", "database": "Database",
    "infrastructure": "Infrastructure", "integration": "Integration / API", "other": "Other",
}


def _install_labels() -> None:
    Priority.__str__ = lambda s: f"{s.key} - {s.name}"
    Account.__str__ = lambda s: s.name
    Team.__str__ = lambda s: s.name
    Tenant.__str__ = lambda s: s.name
    User.__str__ = lambda s: s.display_name
    Service.__str__ = lambda s: s.name
    TicketType.__str__ = lambda s: s.name
    Workflow.__str__ = lambda s: s.name
    Department.__str__ = lambda s: s.name
    State.__str__ = lambda s: f"{s.workflow.name}: {s.name}"
    Ticket.__str__ = lambda s: f"{s.number} {s.subject}"
    ConfigurationItem.__str__ = lambda s: f"{s.name} ({ENV_LABELS.get(s.environment, s.environment)})"
    Category.__str__ = lambda s: f"{s.parent.name} / {s.name}" if s.parent_id else s.name
    ConfigurationItem.type_label = property(lambda s: CI_TYPE_LABELS.get(s.ci_type, s.ci_type))


_install_labels()
