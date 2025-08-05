from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Email(Base):
    __tablename__ = 'emails'
    email_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    plan_id = Column(Integer)
    position_in_plan = Column(Integer)
    title = Column(Text)
    html_content = Column(Text)
    sent = Column(Boolean, default=False)
    send_date = Column(DateTime)

class PastNewsletter(Base):
    __tablename__ = 'past_newsletters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text)
    created_at = Column(DateTime, default=func.now())
    plan_id = Column(Integer)
    Field5 = Column(Integer)

class Newsletter(Base):
    __tablename__ = 'newsletters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text)
    topic = Column(Text)
    demographic = Column(Text)
    title = Column(Text)
    section_1 = Column(Text)
    section_2 = Column(Text)
    section_3 = Column(Text)
    frequency = Column(Text)
    next_send_time = Column(DateTime)
    tone = Column(Text)
    plan_id = Column(Integer)
    section_titles = Column(Text)
    plan_title = Column(Text)
    summary = Column(Text)
    user_id = Column(Integer)
    is_active = Column(Boolean, default=True)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    plan = Column(Text, default='free')
    subscription_id = Column(Text)
    stripe_customer_id = Column(Text)
    subscription_end_date = Column(Text)
    downgrade_to = Column(Text)

class Review(Base):
    __tablename__ = 'reviews'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    stars = Column(Integer, nullable=False)
    comment = Column(Text)

class SchoolNewsletter(Base):
    __tablename__ = 'school_newsletters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer)
    email = Column(Text)
    course_name = Column(Text)
    topics = Column(Text)
    content_types = Column(Text)  # JSON string: ["summary", "quiz", "flashcards"]
    frequency = Column(Text)
    next_send_time = Column(DateTime)
    is_active = Column(Boolean, default=True)
    summary = Column(Text)
