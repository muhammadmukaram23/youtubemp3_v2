# Email Setup Instructions for C3MP Contact Form

## Overview
The contact form on your C3MP website will now send emails directly to your Gmail address (`muhammadmukaram23@gmail.com`) when users submit the form.

## Setup Steps

### 1. Install Required Dependencies
```bash
pip install aiosmtplib email-validator
```

### 2. Set Up Gmail App Password

Since Gmail requires secure authentication, you'll need to create an App Password:

1. **Enable 2-Factor Authentication** on your Gmail account:
   - Go to [Google Account Security](https://myaccount.google.com/security)
   - Enable 2-Step Verification if not already enabled

2. **Generate App Password**:
   - Go to [App Passwords](https://myaccount.google.com/apppasswords)
   - Select "Mail" as the app
   - Copy the 16-character password (e.g., `abcd efgh ijkl mnop`)

### 3. Configure Email Settings

Edit the `email_config.py` file and update these fields:

```python
EMAIL_CONFIG = {
    'SMTP_SERVER': 'smtp.gmail.com',
    'SMTP_PORT': 587,
    'EMAIL_ADDRESS': 'your-gmail@gmail.com',      # Your Gmail address
    'EMAIL_PASSWORD': 'your-16-char-app-password', # The app password from step 2
    'RECIPIENT_EMAIL': 'muhammadmukaram23@gmail.com'
}
```

### 4. Test the System

1. Start your server:
   ```bash
   python main.py
   ```

2. Open `contact.html` in your browser
3. Fill out and submit the contact form
4. Check your email (`muhammadmukaram23@gmail.com`) for the message

## How It Works

- When someone submits the contact form, it sends a POST request to `/contact`
- The backend validates the form data
- An email is sent to `muhammadmukaram23@gmail.com` with:
  - Sender's name and email
  - Subject category
  - Message content
- The sender receives a success confirmation

## Email Format

You'll receive emails like this:

```
Subject: C3MP Contact Form: Technical Support

New contact form submission from C3MP website:

Name: John Doe
Email: john@example.com
Subject: Technical Support

Message:
I'm having trouble with video downloads. Can you help?

---
This email was sent from the C3MP contact form.
Reply directly to this email to respond to the user.
```

## Troubleshooting

### Common Issues:

1. **"Authentication failed"**: Make sure you're using the App Password, not your regular Gmail password
2. **"Connection refused"**: Check your internet connection and firewall settings
3. **Emails not arriving**: Check spam folder, verify the recipient email is correct

### Security Notes:

- Never commit your `email_config.py` file to version control
- Keep your App Password secure
- Consider using environment variables for production deployment

## Alternative Email Providers

If you prefer to use a different email service, update the SMTP settings in `email_config.py`:

**Outlook/Hotmail:**
```python
'SMTP_SERVER': 'smtp-mail.outlook.com',
'SMTP_PORT': 587,
```

**Yahoo Mail:**
```python
'SMTP_SERVER': 'smtp.mail.yahoo.com',
'SMTP_PORT': 587,
```

## Production Deployment

For production, consider:
- Using environment variables for email credentials
- Setting up proper error handling and logging
- Implementing rate limiting to prevent spam
- Adding CAPTCHA to the contact form 