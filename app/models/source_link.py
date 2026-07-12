from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class SourceLink(Base):
    """SQLAlchemy model connecting one unified client to records imported from outside systems.

    Fields:
    - client_id
    - source_system
    - source_record_id
    - confidence_score
    - match_method
    - created_at

    Unique constraint on client_id + source_system + source_record_id.
    """

    __tablename__ = "source_links"
    __table_args__ = (
        UniqueConstraint("client_id", "source_system", "source_record_id", name="uq_source_link"),
    )

    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, nullable=False, index=True)
    source_system = Column(String(100), nullable=False)
    source_record_id = Column(String(255), nullable=False)
    confidence_score = Column(Float, nullable=True)
    match_method = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return (
            f"<SourceLink id={self.id} client_id={self.client_id} "
            f"source_system={self.source_system} source_record_id={self.source_record_id}>"
        )
