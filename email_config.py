"""
Email configuration for C3MP contact form

To set up email functionality:
1. Use a Gmail account (or update SMTP settings for other providers)
2. Enable 2-factor authentication on your Gmail account
3. Generate an App Password: https://support.google.com/accounts/answer/185833
4. Update the EMAIL_ADDRESS and EMAIL_PASSWORD below
5. The RECIPIENT_EMAIL is where contact form messages will be sent

For Gmail App Password setup:
1. Go to your Google Account settings
2. Select Security
3. Under "Signing in to Google," select App passwords
4. Generate a password for "Mail"
5. Use that 16-character password as EMAIL_PASSWORD
"""

# Email configuration
EMAIL_CONFIG = {
    # SMTP server settings (Gmail by default)
    'SMTP_SERVER': 'smtp.gmail.com',
    'SMTP_PORT': 587,
    
    # Sender email credentials (the account that will send emails)
    # UPDATE THESE WITH YOUR ACTUAL CREDENTIALS:
    'EMAIL_ADDRESS': 'yopu meail for google',  # Your Gmail address
    'EMAIL_PASSWORD': 'your password for google app',    # Your Gmail app password (16 characters)
    
    # Recipient email (where contact form messages will be sent)
    'RECIPIENT_EMAIL': 'your email of google'
}

# Alternative SMTP configurations for other email providers:

# Outlook/Hotmail
# EMAIL_CONFIG = {
#     'SMTP_SERVER': 'smtp-mail.outlook.com',
#     'SMTP_PORT': 587,
#     'EMAIL_ADDRESS': 'your-email@outlook.com',
#     'EMAIL_PASSWORD': 'your-password',
#     'RECIPIENT_EMAIL': 'muhammadmukaram23@gmail.com'
# }

# Yahoo Mail
# EMAIL_CONFIG = {
#     'SMTP_SERVER': 'smtp.mail.yahoo.com',
#     'SMTP_PORT': 587,
#     'EMAIL_ADDRESS': 'your-email@yahoo.com',
#     'EMAIL_PASSWORD': 'your-app-password',
#     'RECIPIENT_EMAIL': 'muhammadmukaram23@gmail.com'
# } 