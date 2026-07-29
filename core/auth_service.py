# -*- coding: utf-8 -*-
"""Email verification helpers modeled after the Security_center project."""
import re
import smtplib
from email.message import EmailMessage

from config import (
    AUTH_PREVIEW_CODES,
    EMAIL_CODE_TTL_MINUTES,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_SECURE,
    SMTP_STARTTLS,
    SMTP_USER,
)


EMAIL_PATTERN = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", re.I)


def normalize_email(value):
    email = str(value or "").strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("邮箱格式无效")
    return email


def validate_code(value):
    code = str(value or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("请输入 6 位数字验证码")
    return code


def validate_captcha(value):
    text = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,8}", text):
        raise ValueError("请输入图形验证码")
    return text


def validate_captcha_id(value):
    captcha_id = str(value or "").strip()
    if not re.fullmatch(r"[a-f0-9]{16,80}", captcha_id, flags=re.I):
        raise ValueError("图形验证码无效，请刷新后重试")
    return captcha_id


def send_login_code(recipient, code):
    """Send a login code, or return it only in explicitly enabled preview mode."""
    if not SMTP_HOST:
        if AUTH_PREVIEW_CODES:
            return {"sent": False, "preview_code": code}
        raise RuntimeError("SMTP 未配置，且 AUTH_PREVIEW_CODES 已关闭")

    message = EmailMessage()
    message["Subject"] = "AI Film Studio 登录验证码"
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message.set_content(
        f"你的登录验证码是：{code}\n\n"
        f"验证码 {EMAIL_CODE_TTL_MINUTES} 分钟内有效，请勿转发给他人。"
    )

    if SMTP_SECURE:
        client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    else:
        client = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    try:
        client.ehlo()
        if not SMTP_SECURE and SMTP_STARTTLS:
            client.starttls()
            client.ehlo()
        if SMTP_USER:
            client.login(SMTP_USER, SMTP_PASS)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            client.close()
    return {"sent": True, "preview_code": None}
