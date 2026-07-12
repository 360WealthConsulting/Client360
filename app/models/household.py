import uuid
from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Household(Base):
    __tablename__ = "households"

    household_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False)
    display_name = Column(String(255), nullable=False)
    address = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    zip = Column("zip", String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    clients = relationship("Client", back_populates="household", cascade="all, delete-orphan", lazy="selectin")
