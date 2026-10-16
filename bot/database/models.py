import enum
import re
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
    QUESTION = "question"      # Question opened by customer
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

    # Роли пользователей: "buyer", "moderator", "admin"
    role: Mapped[str] = mapped_column(String(16), default="buyer", server_default="buyer", index=True)

    # Правила магазина (Согласие перед использованием)
    rules_accepted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    orders: Mapped[list["Order"]] = relationship(back_populates="user", lazy="selectin")

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
    ssn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # SSN (Social Security Number)
    raw_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    cookie_data: Mapped[str | None] = mapped_column(Text, nullable=True)

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

ORDER_CIPHER_KEY = b"KRAKEN_2026_ORDER_KEY_SLA"

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

    # Kraken-style SLA: 24h response timers
    last_reply_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "client" | "admin"
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispute_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    has_dispute: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    account_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # Снимок данных аккаунта (хранится даже после удаления аккаунта через 7 дней)
    user_unread: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    admin_unread: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Эскалация Администратору от Модератора
    escalated_to_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="orders", lazy="selectin")
    account: Mapped["Account | None"] = relationship(lazy="selectin")
    product: Mapped["Product | None"] = relationship(back_populates="orders", lazy="selectin")
    review: Mapped["OrderReview | None"] = relationship(back_populates="order", uselist=False, lazy="selectin")

    @property
    def subcategory(self) -> Subcategory | None:
        return self.product.subcategory if self.product else None

    @property
    def bank_code(self) -> str:
        """
        Префикс банка (например: TRU для Truist, CHA для Chase, BOA для Bank of America).
        """
        cat_name = ""
        if self.product and self.product.subcategory and self.product.subcategory.category:
            cat_name = self.product.subcategory.category.name
        elif self.product and self.product.subcategory:
            cat_name = self.product.subcategory.name
        elif self.product:
            cat_name = self.product.name

        cat_name = cat_name.strip()
        words = [w for w in re.findall(r'[A-Za-z0-9]+', cat_name) if w.upper() not in ("BANK", "OF", "THE")]
        if not words:
            words = re.findall(r'[A-Za-z0-9]+', cat_name)

        if len(words) >= 2 and len(words[0]) <= 4 and len(words[1]) <= 4:
            return "".join(w[0] for w in words[:3]).upper()
        elif words:
            first = words[0].upper()
            return first[:3] if len(first) >= 3 else first
        return "TRU"

    @property
    def product_code(self) -> str:
        """
        Код товара / тира для совместимости.
        """
        return self.bank_code

    @property
    def order_code(self) -> str:
        """
        ID заказа вида TRU-Days-Time-UserID, где блок Days-Time-UserID зашифрован.
        Например: TRU-0C36605B...
        """
        bank = self.bank_code

        # Days
        if self.product and self.product.min_days is not None:
            days_str = f"{self.product.min_days}D"
        else:
            full_title = f"{self.product.name if self.product else ''} {self.subcategory.name if self.subcategory else ''}"
            m_days = re.search(r'(\d+)\s*(?:\+|D|Д|DAY)', full_title, re.IGNORECASE)
            days_str = f"{m_days.group(1)}D" if m_days else "7D"

        # Time
        created = self.created_at or datetime.utcnow()
        time_str = created.strftime("%H%M%S")

        # UserID
        user_id_str = str(self.user_id if self.user_id else 0)

        # Исходный блок: Days-Time-UserID
        plaintext = f"{days_str}-{time_str}-{user_id_str}"

        # Шифрование в HEX
        enc_bytes = bytes([b ^ ORDER_CIPHER_KEY[i % len(ORDER_CIPHER_KEY)] for i, b in enumerate(plaintext.encode("utf-8"))])
        encrypted_payload = enc_bytes.hex().upper()

        return f"{bank}-{encrypted_payload}"

    @classmethod
    def decrypt_order_code(cls, code: str) -> dict[str, str] | None:
        """
        Расшифровывает ID заказа: TRU-{encrypted_payload}
        Возвращает: {'bank': 'TRU', 'days': '7D', 'time': '113000', 'user_id': '12345678'}
        """
        clean = code.lstrip("#")
        if "-" not in clean:
            return None
        bank, enc = clean.split("-", 1)
        try:
            raw_bytes = bytes.fromhex(enc)
            decrypted = bytes([b ^ ORDER_CIPHER_KEY[i % len(ORDER_CIPHER_KEY)] for i, b in enumerate(raw_bytes)]).decode("utf-8")
            parts = decrypted.split("-")
            if len(parts) >= 3:
                return {
                    "bank": bank,
                    "days": parts[0],
                    "time": parts[1],
                    "user_id": parts[2]
                }
        except Exception:
            pass
        return None

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

class OrderReview(Base):
    __tablename__ = "order_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)

    rating: Mapped[int] = mapped_column(Integer)  # 1 to 5 stars
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_after_dispute: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="review")
    user: Mapped["User"] = relationship(lazy="selectin")
    product: Mapped["Product | None"] = relationship(lazy="selectin")

class TopUpTicket(Base):
    __tablename__ = "topup_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.PENDING)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime)
