import enum
from datetime import datetime, date
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class TicketStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"

class OrderStatus(str, enum.Enum):
    HOLD = "hold"              # Frozen for 24h verification window
    COMPLETED = "completed"    # Released to admin/store
    DISPUTED = "disputed"      # Dispute opened by customer
    REFUNDED = "refunded"      # Refunded to customer

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tg_id", name="uq_users_tg_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)

    referrer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.tg_id"), nullable=True)

    tron_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tron_private_key: Mapped[str] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    subcategories: Mapped[list["Subcategory"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

class Subcategory(Base):
    __tablename__ = "subcategories"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)

    category: Mapped["Category"] = relationship(back_populates="subcategories", lazy="selectin")
    products: Mapped[list["Product"]] = relationship(
        back_populates="subcategory",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    subcategory_id: Mapped[int] = mapped_column(ForeignKey("subcategories.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(Numeric(12, 2), default=1.0)
    min_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    max_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    subcategory: Mapped["Subcategory"] = relationship(back_populates="products", lazy="selectin")
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="product",
        lazy="selectin"
    )

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(16), default="US", index=True)  # Format: "CA", "NY"
    data: Mapped[str] = mapped_column(Text)  # Format: "ST | NAME SURNAME | DD.MM"
    name: Mapped[str] = mapped_column(String(255), index=True)  # Format: "NAME SURNAME"
    date_info: Mapped[str] = mapped_column(String(32), index=True)  # Format: "DD.MM"
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_sold: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sold_to_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.tg_id"), nullable=True)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    product: Mapped["Product"] = relationship(back_populates="accounts", lazy="selectin")

    @property
    def age_days(self) -> int:
        if not self.registration_date:
            return 0
        return max(0, (date.today() - self.registration_date).days)

    @property
    def subcategory(self) -> Subcategory | None:
        return self.product.subcategory if self.product else None

    @property
    def subcategory_id(self) -> int | None:
        return self.product.subcategory_id if self.product else None

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.HOLD, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    hold_until: Mapped[datetime] = mapped_column(DateTime, index=True)
    disputed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(lazy="selectin")
    account: Mapped["Account | None"] = relationship(lazy="selectin")
    product: Mapped["Product | None"] = relationship(back_populates="orders", lazy="selectin")

    @property
    def subcategory(self) -> Subcategory | None:
        return self.product.subcategory if self.product else None
    messages: Mapped[list["DisputeMessage"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="DisputeMessage.created_at.asc()",
        lazy="selectin"
    )

class DisputeMessage(Base):
    __tablename__ = "dispute_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, index=True)
    sender_role: Mapped[str] = mapped_column(String(16))  # "client" | "admin"
    text: Mapped[str] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="messages")

class TopUpTicket(Base):
    __tablename__ = "topup_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.PENDING)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
