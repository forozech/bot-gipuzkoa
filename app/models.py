from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base
from sqlalchemy import Column, Integer, String, Date, Numeric, ForeignKey
from sqlalchemy.orm import relationship


class Meta(Base):
    __tablename__ = "meta"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class Notice(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True)
    code = Column(String)
    object = Column(Text)

    first_publication_date = Column(String)
    last_publication_date = Column(String)

    contracting_authority_name = Column(String)
    contracting_authority_scope = Column(String)

    contract_type_id = Column(Integer)
    procedure_status_id = Column(Integer)

    deadline_date = Column(String)
    budget_without_vat = Column(Float)

    main_entity_of_page = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow)

    contracts = relationship("Contract", back_populates="notice")

class Contract(Base):
    __tablename__ = "contracts"

    id = Column(String, primary_key=True)
    contracting_notice_id = Column(Integer, ForeignKey("notices.id"))

    object = Column(Text)
    contract_type_id = Column(Integer)
    procedure_status_id = Column(Integer)
    procedure_type_id = Column(Integer)

    award_date = Column(String)
    contract_end_date = Column(String)
    award_amount = Column(Float)
    award_amount_without_vat = Column(Float)
    months_contract_duration = Column(Integer)

    cpv = Column(String)
    minor_contract = Column(Boolean)

    main_entity_of_page = Column(String)

    notice = relationship("Notice", back_populates="contracts")
