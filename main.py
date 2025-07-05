from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Depends, Response, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, HttpUrl, validator, EmailStr
from typing import Optional, List, Dict, Any
import yt_dlp
import os
import uuid
import asyncio
import aiofiles
from datetime import datetime, timedelta
import logging
from pathlib import Path
import shutil
from enum import Enum
import json
import re
from urllib.parse import urlparse, parse_qs
import zipfile
import io
import lameenc
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Import pure Python audio processing libraries
try:
    from pydub import AudioSegment
    import lameenc
    PURE_PYTHON_MP3_AVAILABLE = True
except ImportError:
    PURE_PYTHON_MP3_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="YouTube to MP3 Converter API",
    description="Convert YouTube videos to MP3 with various audio quality options",
    version="1.0.0"
)

# Session middleware (add before CORS)
app.add_middleware(SessionMiddleware, secret_key="your-secret-key-change-in-production")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Audio quality enum
class AudioQuality(str, Enum):
    LOW = "low"      # 96kbps
    MEDIUM = "medium" # 128kbps
    HIGH = "high"    # 192kbps
    ULTRA = "ultra"  # 320kbps

# Video quality enum
class VideoQuality(str, Enum):
    LOW = "360p"     # 360p
    MEDIUM = "480p"  # 480p
    HIGH = "720p"    # 720p HD
    ULTRA = "1080p"  # 1080p Full HD
    BEST = "best"    # Best available quality

# Pydantic models
class DownloadRequest(BaseModel):
    url: HttpUrl
    quality: AudioQuality = AudioQuality.MEDIUM
    start_time: Optional[int] = None  # Start time in seconds
    end_time: Optional[int] = None    # End time in seconds
    
    @validator('url')
    def validate_youtube_url(cls, v):
        url_str = str(v)
        if not any(domain in url_str for domain in ['youtube.com', 'youtu.be', 'music.youtube.com']):
            raise ValueError('URL must be a valid YouTube URL')
        return v

class VideoInfo(BaseModel):
    id: str
    title: str
    duration: int
    uploader: str
    view_count: int
    upload_date: str
    thumbnail: str
    description: str

class DownloadResponse(BaseModel):
    task_id: str
    status: str
    message: str

class TaskStatus(BaseModel):
    task_id: str
    status: str
    progress: float
    message: str
    download_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None

class VideoDownloadRequest(BaseModel):
    url: HttpUrl
    quality: VideoQuality = VideoQuality.HIGH
    start_time: Optional[int] = None  # Start time in seconds
    end_time: Optional[int] = None    # End time in seconds
    
    @validator('url')
    def validate_youtube_url(cls, v):
        url_str = str(v)
        if not any(domain in url_str for domain in ['youtube.com', 'youtu.be', 'music.youtube.com']):
            raise ValueError('URL must be a valid YouTube URL')
        return v

class PlaylistRequest(BaseModel):
    url: HttpUrl
    quality: AudioQuality = AudioQuality.MEDIUM
    max_videos: Optional[int] = 10

class ContactForm(BaseModel):
    firstName: str
    lastName: str
    email: str  # Temporarily changed from EmailStr to str for debugging
    subject: str
    message: str
    
    @validator('firstName', 'lastName')
    def validate_names(cls, v):
        if len(v.strip()) < 2:
            raise ValueError('Name must be at least 2 characters long')
        return v.strip()
    
    @validator('email')
    def validate_email(cls, v):
        if '@' not in v or '.' not in v:
            raise ValueError('Invalid email format')
        return v.strip()
    
    @validator('subject')
    def validate_subject(cls, v):
        allowed_subjects = [
            'technical-support', 'bug-report', 'feature-request', 
            'api-question', 'general-inquiry', 'other'
        ]
        if v not in allowed_subjects:
            raise ValueError('Invalid subject')
        return v
    
    @validator('message')
    def validate_message(cls, v):
        if len(v.strip()) < 10:
            raise ValueError('Message must be at least 10 characters long')
        return v.strip()

# Global storage for tasks and downloads
tasks: Dict[str, Dict[str, Any]] = {}
completed_tasks: Dict[str, Dict[str, Any]] = {}  # Store completed task metadata
session_files: Dict[str, List[str]] = {}  # Map session_id to list of task_ids
downloads_dir = Path("downloads")
downloads_dir.mkdir(exist_ok=True)

# FFmpeg path configuration
ffmpeg_path = None

# Email configuration
try:
    from email_config import EMAIL_CONFIG
except ImportError:
    # Fallback configuration if email_config.py doesn't exist
    EMAIL_CONFIG = {
        'SMTP_SERVER': 'smtp.gmail.com',
        'SMTP_PORT': 587,
        'EMAIL_ADDRESS': 'your-email@gmail.com',  # You'll need to set this
        'EMAIL_PASSWORD': 'your-app-password',    # You'll need to set this
        'RECIPIENT_EMAIL': 'muhammadmukaram23@gmail.com'
    }
    logger.warning("email_config.py not found. Using default email configuration. Please create email_config.py with your email credentials.")

# Audio Quality settings
QUALITY_SETTINGS = {
    AudioQuality.LOW: {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '96',
        }]
    },
    AudioQuality.MEDIUM: {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }]
    },
    AudioQuality.HIGH: {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    },
    AudioQuality.ULTRA: {
        'format': 'bestaudio[ext=m4a]/bestaudio',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }]
    }
}

# Video Quality settings
VIDEO_QUALITY_SETTINGS = {
    VideoQuality.LOW: {
        'format': 'best[height<=360]/worst',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    },
    VideoQuality.MEDIUM: {
        'format': 'best[height<=480]/best[height<=720]/worst',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    },
    VideoQuality.HIGH: {
        'format': 'best[height<=720]/best[height<=1080]/worst',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    },
    VideoQuality.ULTRA: {
        'format': 'best[height<=1080]/best[height<=1440]/worst',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    },
    VideoQuality.BEST: {
        'format': 'best',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }]
    }
}

# Helper functions
def clean_youtube_url(url: str) -> str:
    """Remove playlist parameters from YouTube URL to get single video"""
    try:
        # Handle youtu.be URLs first
        if 'youtu.be/' in url:
            video_id = url.split('youtu.be/')[1].split('?')[0].split('&')[0]
            return f"https://www.youtube.com/watch?v={video_id}"
        
        # Handle regular YouTube URLs
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        # Keep only the video ID parameter
        if 'v' in query_params:
            video_id = query_params['v'][0]
            return f"https://www.youtube.com/watch?v={video_id}"
        
        # Handle YouTube Music URLs
        if 'music.youtube.com' in url and 'v=' in url:
            video_id = url.split('v=')[1].split('&')[0]
            return f"https://www.youtube.com/watch?v={video_id}"
            
        return url
    except Exception as e:
        logger.warning(f"URL cleaning failed: {str(e)}, using original URL")
        return url

def get_ydl_opts(quality: AudioQuality, output_path: str, start_time: int = None, end_time: int = None):
    opts = {
        'outtmpl': output_path,
        'format': QUALITY_SETTINGS[quality]['format'],
        'postprocessors': QUALITY_SETTINGS[quality]['postprocessors'],
        'no_warnings': True,
        'extractaudio': True,
        'audioformat': 'mp3',
        'noplaylist': True,  # Only download single video, ignore playlist
        # Add user agent and other headers to bypass bot detection
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip,deflate',
            'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
            'Keep-Alive': '115',
            'Connection': 'keep-alive',
        },
        # Additional options to bypass restrictions
        'extractor_args': {
            'youtube': {
                'skip': ['dash', 'hls'],
                'player_skip': ['configs'],
            }
        },
        # Use embedded player to avoid some restrictions
        'embed_subs': False,
        'age_limit': None,
        'retries': 3,
        'fragment_retries': 3,
    }
    
    # Add FFmpeg path if configured
    if ffmpeg_path:
        opts['ffmpeg_location'] = ffmpeg_path
    
    # Add time range if specified
    if start_time is not None or end_time is not None:
        postprocessor = {
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': QUALITY_SETTINGS[quality]['postprocessors'][0]['preferredquality'],
        }
        
        if start_time is not None:
            opts['download_ranges'] = [{'start_time': start_time, 'end_time': end_time}]
        
        opts['postprocessors'] = [postprocessor]
    
    return opts

def get_video_ydl_opts(quality: VideoQuality, output_path: str, start_time: int = None, end_time: int = None):
    opts = {
        'outtmpl': output_path,
        'format': VIDEO_QUALITY_SETTINGS[quality]['format'],
        'no_warnings': True,
        'noplaylist': True,  # Only download single video, ignore playlist
    }
    
    # Add FFmpeg path if configured
    if ffmpeg_path:
        opts['ffmpeg_location'] = ffmpeg_path
    
    # Add postprocessors for video conversion
    if 'postprocessors' in VIDEO_QUALITY_SETTINGS[quality]:
        opts['postprocessors'] = VIDEO_QUALITY_SETTINGS[quality]['postprocessors']
    
    # Add time range if specified
    if start_time is not None or end_time is not None:
        if start_time is not None:
            opts['download_ranges'] = [{'start_time': start_time, 'end_time': end_time}]
    
    return opts

def progress_hook(d):
    """Progress hook for yt-dlp"""
    if d['status'] == 'downloading':
        task_id = d.get('task_id')
        if task_id and task_id in tasks:
            if '_percent_str' in d:
                percent_str = d['_percent_str'].strip().replace('%', '')
                try:
                    progress = float(percent_str)
                    tasks[task_id]['progress'] = progress
                    tasks[task_id]['message'] = f"Downloading: {percent_str}%"
                except ValueError:
                    pass
    elif d['status'] == 'finished':
        task_id = d.get('task_id')
        if task_id and task_id in tasks:
            tasks[task_id]['progress'] = 100.0
            tasks[task_id]['message'] = "Processing audio..."

async def send_contact_email(contact_data: ContactForm):
    """Send contact form email"""
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['EMAIL_ADDRESS']
        msg['To'] = EMAIL_CONFIG['RECIPIENT_EMAIL']
        msg['Subject'] = f"C3MP Contact Form: {contact_data.subject.replace('-', ' ').title()}"
        
        # Create email body
        subject_mapping = {
            'technical-support': 'Technical Support',
            'bug-report': 'Bug Report',
            'feature-request': 'Feature Request',
            'api-question': 'API Question',
            'general-inquiry': 'General Inquiry',
            'other': 'Other'
        }
        
        body = f"""
New contact form submission from C3MP website:

Name: {contact_data.firstName} {contact_data.lastName}
Email: {contact_data.email}
Subject: {subject_mapping.get(contact_data.subject, contact_data.subject)}

Message:
{contact_data.message}

---
This email was sent from the C3MP contact form.
Reply directly to this email to respond to the user.
        """
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Send email
        await aiosmtplib.send(
            msg,
            hostname=EMAIL_CONFIG['SMTP_SERVER'],
            port=EMAIL_CONFIG['SMTP_PORT'],
            start_tls=True,
            username=EMAIL_CONFIG['EMAIL_ADDRESS'],
            password=EMAIL_CONFIG['EMAIL_PASSWORD']
        )
        
        logger.info(f"Contact email sent successfully from {contact_data.email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send contact email: {str(e)}")
        return False

# Helper function for pure Python MP3 conversion
async def convert_to_mp3_python(input_file: Path, output_file: Path, quality: AudioQuality) -> bool:
    """Convert audio file to MP3 using pure Python libraries"""
    try:
        # Map quality to bitrate
        bitrate_map = {
            AudioQuality.LOW: 96,
            AudioQuality.MEDIUM: 128,
            AudioQuality.HIGH: 192,
            AudioQuality.ULTRA: 320
        }
        bitrate = bitrate_map.get(quality, 128)
        
        logger.info(f"Converting {input_file} to MP3 using pure Python (bitrate: {bitrate}k)")
        
        # Load the audio file
        audio = AudioSegment.from_file(str(input_file))
        
        # Export as MP3 using pydub's built-in export
        audio.export(
            str(output_file),
            format="mp3",
            bitrate=f"{bitrate}k"
        )
        
        logger.info(f"Pure Python MP3 conversion successful: {output_file}")
        return True
    except Exception as e:
        logger.error(f"Pure Python MP3 conversion failed: {str(e)}")
        return False

async def convert_to_mp3_direct(input_file: Path, output_file: Path, quality: AudioQuality) -> bool:
    """Convert audio file to MP3 using pydub with lameenc backend"""
    try:
        # Map quality to bitrate
        bitrate_map = {
            AudioQuality.LOW: "96k",
            AudioQuality.MEDIUM: "128k",
            AudioQuality.HIGH: "192k",
            AudioQuality.ULTRA: "320k"
        }
        bitrate = bitrate_map.get(quality, "128k")
        
        logger.info(f"Converting {input_file} to MP3 using pydub+lameenc (bitrate: {bitrate})")
        
        # Import pydub here to handle import errors gracefully
        from pydub import AudioSegment
        from pydub.utils import which
        
        # Check if lameenc is available
        if not which("lame"):
            logger.warning("LAME encoder not found in PATH, trying to use built-in lameenc")
        
        # Load the audio file using pydub
        audio = AudioSegment.from_file(str(input_file))
        
        # Try to export with lameenc parameters
        try:
            # Export to MP3 with specified bitrate using lameenc
            audio.export(
                str(output_file),
                format="mp3",
                bitrate=bitrate,
                parameters=["-q:a", "2"]  # High quality encoding
            )
        except Exception as export_error:
            logger.warning(f"Failed to export with lameenc parameters: {export_error}")
            # Fallback: try basic MP3 export without custom parameters
            audio.export(
                str(output_file),
                format="mp3",
                bitrate=bitrate
            )
        
        # Verify the output file was created
        if output_file.exists() and output_file.stat().st_size > 0:
            logger.info(f"MP3 conversion successful: {output_file}")
            return True
        else:
            logger.error("MP3 file was not created or is empty")
            return False
            
    except ImportError as ie:
        logger.error(f"Import error during MP3 conversion: {str(ie)}")
        return False
    except Exception as e:
        logger.error(f"Direct MP3 conversion failed: {str(e)}")
        return False

async def convert_to_mp3_ytdlp(input_file: Path, output_file: Path, quality: AudioQuality) -> bool:
    """Convert audio file to MP3 using yt-dlp's built-in conversion (no external dependencies)"""
    try:
        # Map quality to bitrate
        bitrate_map = {
            AudioQuality.LOW: "96",
            AudioQuality.MEDIUM: "128", 
            AudioQuality.HIGH: "192",
            AudioQuality.ULTRA: "320"
        }
        bitrate = bitrate_map.get(quality, "128")
        
        logger.info(f"Converting {input_file} to MP3 using yt-dlp (bitrate: {bitrate}k)")
        
        # Create a temporary yt-dlp configuration for post-processing
        temp_output = output_file.with_suffix('.temp.mp3')
        
        ydl_opts = {
            'outtmpl': str(temp_output.with_suffix('')),
            'format': 'bestaudio',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': bitrate,
            }],
            'no_warnings': True,
        }
        
        # Add FFmpeg path if configured
        if ffmpeg_path:
            ydl_opts['ffmpeg_location'] = ffmpeg_path
        
        # Copy the input file to a temporary location with a name yt-dlp can process
        import shutil
        temp_input = downloads_dir / f"temp_input_{input_file.stem}.{input_file.suffix[1:]}"
        shutil.copy2(input_file, temp_input)
        
        try:
            # Use yt-dlp to convert the file
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Process the temporary file as if it were a URL
                ydl.process_info({
                    'url': str(temp_input),
                    'title': input_file.stem,
                    'id': input_file.stem,
                    'ext': input_file.suffix[1:],
                })
            
            # Check if conversion was successful and move the file
            expected_output = temp_output.with_suffix('.mp3')
            if expected_output.exists():
                shutil.move(expected_output, output_file)
                logger.info(f"yt-dlp MP3 conversion successful: {output_file}")
                return True
            else:
                logger.error("yt-dlp conversion did not produce expected output file")
                return False
                
        finally:
            # Clean up temporary files
            if temp_input.exists():
                temp_input.unlink()
            if temp_output.exists():
                temp_output.unlink()
                
    except Exception as e:
        logger.error(f"yt-dlp MP3 conversion failed: {str(e)}")
        return False

async def convert_with_simple_copy(input_file: Path, output_file: Path) -> bool:
    """Simple fallback: copy the file with MP3 extension (for compatibility)"""
    try:
        import shutil
        logger.info(f"Using simple copy fallback: {input_file} -> {output_file}")
        shutil.copy2(input_file, output_file)
        
        if output_file.exists() and output_file.stat().st_size > 0:
            logger.info(f"File copy successful: {output_file}")
            return True
        else:
            logger.error("File copy failed")
            return False
            
    except Exception as e:
        logger.error(f"Simple copy failed: {str(e)}")
        return False

async def cleanup_temp_directory(temp_dir_path: Path, task_id: str):
    """Clean up temporary directory after file has been served (SESSION-BASED - NO IMMEDIATE CLEANUP)"""
    try:
        # For session-based storage, we DON'T clean up immediately
        # Files will be cleaned up when the session ends (browser close/reload)
        logger.info(f"File {task_id} will be kept until session ends")
        
        # Mark task as completed but keep files
        if task_id in tasks:
            tasks[task_id]['status'] = 'completed'
            tasks[task_id]['completed_at'] = datetime.now().isoformat()
            logger.info(f"Task {task_id} marked as completed (files kept for session)")
            
    except Exception as e:
        logger.error(f"Failed to update task status {task_id}: {str(e)}")

def cleanup_old_temp_directories():
    """Clean up any leftover temp directories from previous runs"""
    try:
        for temp_dir in Path(".").glob("temp_*"):
            if temp_dir.is_dir():
                try:
                    shutil.rmtree(temp_dir)
                    logger.info(f"Cleaned up leftover temp directory: {temp_dir}")
                except Exception as e:
                    logger.warning(f"Could not clean up leftover temp directory {temp_dir}: {str(e)}")
    except Exception as e:
        logger.error(f"Error during startup cleanup: {str(e)}")

# Helper function to get or create session ID
def get_session_id(request: Request) -> str:
    """Get or create a session ID for the user"""
    session_id = request.session.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())
        request.session['session_id'] = session_id
        session_files[session_id] = []
    elif session_id not in session_files:
        session_files[session_id] = []
    return session_id

# Helper function to cleanup session files
def cleanup_session_files(session_id: str):
    """Clean up all files associated with a session"""
    if session_id in session_files:
        cleaned_count = 0
        for task_id in session_files[session_id]:
            # Only clean up completed or failed tasks, not active ones
            if task_id in tasks:
                task_status = tasks[task_id].get('status', 'unknown')
                if task_status in ['completed', 'failed']:
                    # Clean up temp directory if it exists
                    temp_dir = Path(f"temp_{task_id}")
                    if temp_dir.exists():
                        try:
                            shutil.rmtree(temp_dir)
                            logger.info(f"Cleaned up session temp directory: {temp_dir}")
                            cleaned_count += 1
                        except Exception as e:
                            logger.warning(f"Could not clean up session temp directory {temp_dir}: {str(e)}")
                    
                    # Remove from tasks
                    del tasks[task_id]
                    
                    # Remove from completed_tasks if there
                    if task_id in completed_tasks:
                        del completed_tasks[task_id]
                else:
                    logger.info(f"Skipping cleanup of active task {task_id} with status: {task_status}")
            else:
                # Task not in memory, try to clean up temp directory anyway
                temp_dir = Path(f"temp_{task_id}")
                if temp_dir.exists():
                    try:
                        shutil.rmtree(temp_dir)
                        logger.info(f"Cleaned up orphaned temp directory: {temp_dir}")
                        cleaned_count += 1
                    except Exception as e:
                        logger.warning(f"Could not clean up orphaned temp directory {temp_dir}: {str(e)}")
        
        # Clear session file list
        del session_files[session_id]
        logger.info(f"Cleaned up session {session_id} - {cleaned_count} files/directories removed")

# Clean up any leftover temp directories on startup
cleanup_old_temp_directories()

# Mount static files and HTML routes
@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def serve_index():
    """Serve the main index.html page"""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Index.html not found</h1>", status_code=404)

@app.get("/api", response_class=HTMLResponse)
@app.get("/api.html", response_class=HTMLResponse)
async def serve_api_page():
    """Serve the API documentation page"""
    try:
        with open("api.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>API.html not found</h1>", status_code=404)

@app.get("/contact", response_class=HTMLResponse)
@app.get("/contact.html", response_class=HTMLResponse)
async def serve_contact_page():
    """Serve the contact page"""
    try:
        with open("contact.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Contact.html not found</h1>", status_code=404)

@app.get("/faqs", response_class=HTMLResponse)
@app.get("/faqs.html", response_class=HTMLResponse)
async def serve_faqs_page():
    """Serve the FAQs page"""
    try:
        with open("faqs.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>FAQs.html not found</h1>", status_code=404)

@app.get("/changelog", response_class=HTMLResponse)
@app.get("/changelog.html", response_class=HTMLResponse)
async def serve_changelog_page():
    """Serve the changelog page"""
    try:
        with open("changelog.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Changelog.html not found</h1>", status_code=404)

@app.get("/favicon.ico")
async def favicon():
    """Handle favicon requests"""
    # Return a simple response for favicon to avoid 404 errors
    return Response(content="", media_type="image/x-icon")

@app.post("/cleanup-session")
async def cleanup_session_endpoint(request: Request):
    """Clean up session files when user leaves the page"""
    try:
        session_id = request.session.get('session_id')
        if session_id:
            cleanup_session_files(session_id)
            return {"message": "Session cleaned up successfully"}
        return {"message": "No session to clean up"}
    except Exception as e:
        logger.error(f"Session cleanup failed: {str(e)}")
        return {"message": "Session cleanup failed", "error": str(e)}

async def download_video(task_id: str, url: str, quality: AudioQuality, start_time: int = None, end_time: int = None):
    """Background task to download and convert video"""
    logger.info(f"Starting download_video for task: {task_id}")
    
    # Ensure task exists before starting
    if task_id not in tasks:
        logger.error(f"Task {task_id} not found when starting download_video")
        return
    
    try:
        tasks[task_id]['status'] = 'processing'
        tasks[task_id]['message'] = 'Starting download...'
        logger.info(f"Task {task_id} status updated to processing")
        
        # Clean URL to remove playlist parameters
        clean_url = clean_youtube_url(url)
        logger.info(f"Original URL: {url}")
        logger.info(f"Cleaned URL: {clean_url}")
        
        # First get video info to store title early
        try:
            with yt_dlp.YoutubeDL({'no_warnings': True, 'noplaylist': True}) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                video_title = info.get('title', 'Unknown')
                tasks[task_id]['title'] = video_title
                logger.info(f"Video title: {video_title}")
        except Exception as e:
            logger.warning(f"Could not get video title: {str(e)}")
            video_title = 'Unknown'
        
        # Sanitize video title for filename
        def sanitize_filename(filename):
            # Remove invalid filename characters
            sanitized = re.sub(r'[\\/*?:"<>|]', '', filename)
            # Replace multiple spaces with single space
            sanitized = re.sub(r'\s+', ' ', sanitized)
            # Trim and limit length
            sanitized = sanitized.strip()[:100]  # Limit to 100 characters
            return sanitized if sanitized else 'Unknown'
        
        sanitized_title = sanitize_filename(video_title)
        
        # Create temporary directory for this task
        temp_dir = Path(f"temp_{task_id}")
        temp_dir.mkdir(exist_ok=True)
        
        # Create unique filename for temporary download
        output_filename = f"{task_id}.%(ext)s"
        output_path = str(temp_dir / output_filename)
        
        # More reliable download options with bot detection bypass
        ydl_opts = {
            'outtmpl': output_path,
            'format': 'bestaudio/best',
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [progress_hook],
            'extract_flat': False,
            'writethumbnail': False,
            'writeinfojson': False,
            'writesubtitles': False,
            'writeautomaticsub': False,
            'ignoreerrors': False,
            'retries': 3,
            'fragment_retries': 3,
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            # Use embedded player to avoid some restrictions
            'embed_subs': False,
            'age_limit': None,
        }
        
        # Add FFmpeg path if configured
        if ffmpeg_path:
            ydl_opts['ffmpeg_location'] = ffmpeg_path
        
        # Download the audio with retry logic
        tasks[task_id]['progress'] = 20.0
        tasks[task_id]['message'] = 'Downloading audio...'
        
        download_success = False
        max_retries = 2
        
        for attempt in range(max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Add task_id to the progress hook context
                    ydl._progress_hooks[0] = lambda d: progress_hook({**d, 'task_id': task_id})
                    ydl.download([clean_url])
                download_success = True
                break
            except Exception as e:
                logger.error(f"Download attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries:
                    tasks[task_id]['message'] = f'Download failed, retrying... (attempt {attempt + 2})'
                    await asyncio.sleep(2)  # Wait before retry
                else:
                    raise Exception(f"Download failed after {max_retries + 1} attempts: {str(e)}")
        
        if download_success:
            tasks[task_id]['progress'] = 70.0
            tasks[task_id]['message'] = 'Download complete, processing...'
        
        # Check for downloaded file with better detection
        original_file = None
        possible_extensions = ['m4a', 'webm', 'mp3', 'opus', 'aac', 'mp4']
        
        # First check exact task_id matches
        for ext in possible_extensions:
            potential_file = temp_dir / f"{task_id}.{ext}"
            if potential_file.exists() and potential_file.stat().st_size > 0:
                original_file = potential_file
                logger.info(f"Found downloaded {ext.upper()} file: {potential_file}")
                break
        
        # If not found, check all files in temp directory
        if not original_file:
            for file_path in temp_dir.iterdir():
                if file_path.is_file() and file_path.stat().st_size > 0:
                    # Check if it's an audio/video file
                    if file_path.suffix.lower() in ['.m4a', '.webm', '.mp3', '.opus', '.aac', '.mp4', '.mkv']:
                        original_file = file_path
                        logger.info(f"Found downloaded file: {original_file}")
                        break
            
        if not original_file:
            # List all files in temp directory for debugging
            files_in_dir = list(temp_dir.iterdir())
            logger.error(f"No audio file found in {temp_dir}. Files present: {files_in_dir}")
            raise Exception(f"Failed to download audio file. No valid audio file found in temporary directory.")
        
        # Create final MP3 filename with sanitized title
        final_mp3_filename = f"{sanitized_title}.mp3"
        mp3_file = temp_dir / final_mp3_filename
        
        # If file already exists, add a number suffix
        counter = 1
        while mp3_file.exists():
            final_mp3_filename = f"{sanitized_title} ({counter}).mp3"
            mp3_file = temp_dir / final_mp3_filename
            counter += 1
        
        conversion_success = False
        
        # Optimize conversion - try fastest methods first
        tasks[task_id]['progress'] = 85.0
        tasks[task_id]['message'] = 'Converting to MP3...'
        
        # Method 1: If already MP3, just copy (fastest)
        if original_file.suffix.lower() == '.mp3':
            logger.info("File already MP3, using direct copy...")
            try:
                conversion_success = await convert_with_simple_copy(original_file, mp3_file)
            except Exception as e:
                logger.error(f"Direct copy failed: {str(e)}")
        
        # Method 2: Try pydub (fast pure Python method)
        if not conversion_success and PURE_PYTHON_MP3_AVAILABLE:
            tasks[task_id]['message'] = "Converting to MP3..."
            logger.info("Using pydub for MP3 conversion...")
            try:
                conversion_success = await convert_to_mp3_python(original_file, mp3_file, quality)
            except Exception as e:
                logger.error(f"Pydub conversion error: {str(e)}")
        
        # Method 3: Try direct lameenc conversion
        if not conversion_success:
            logger.info("Using lameenc for MP3 conversion...")
            try:
                conversion_success = await convert_to_mp3_direct(original_file, mp3_file, quality)
            except Exception as e:
                logger.error(f"Lameenc conversion error: {str(e)}")
        
        # Method 4: Try yt-dlp conversion
        if not conversion_success:
            logger.info("Using yt-dlp for MP3 conversion...")
            try:
                conversion_success = await convert_to_mp3_ytdlp(original_file, mp3_file, quality)
            except Exception as e:
                logger.error(f"yt-dlp conversion error: {str(e)}")
        
        # Method 5: FFmpeg as last resort (slower but reliable)
        if not conversion_success and ffmpeg_path:
            tasks[task_id]['message'] = "Converting using FFmpeg..."
            logger.info("Using FFmpeg for MP3 conversion...")
            try:
                # Use yt-dlp's postprocessor with FFmpeg
                ydl_opts = get_ydl_opts(quality, output_path, start_time, end_time)
                ydl_opts['ffmpeg_location'] = ffmpeg_path
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl._progress_hooks[0] = lambda d: progress_hook({**d, 'task_id': task_id})
                    ydl.download([clean_url])
                    
                # Check if FFmpeg created the MP3 with task_id name
                temp_mp3 = temp_dir / f"{task_id}.mp3"
                if temp_mp3.exists():
                    # Move to final filename
                    import shutil
                    shutil.move(temp_mp3, mp3_file)
                    conversion_success = True
                    logger.info("FFmpeg conversion successful")
            except Exception as e:
                logger.error(f"FFmpeg conversion failed: {str(e)}")
        
        # Method 6: Simple copy as final fallback
        if not conversion_success:
            logger.info("Using simple copy as fallback...")
            try:
                conversion_success = await convert_with_simple_copy(original_file, mp3_file)
            except Exception as e:
                logger.error(f"Simple copy failed: {str(e)}")
        
        # Update task status based on conversion result
        if conversion_success and mp3_file.exists():
            # MP3 conversion successful - delete the original file
            try:
                if original_file.exists():
                    original_file.unlink()
                    logger.info(f"Deleted original file: {original_file}")
            except Exception as e:
                logger.warning(f"Could not delete original file {original_file}: {str(e)}")
            
            tasks[task_id]['status'] = 'completed'
            tasks[task_id]['progress'] = 100.0
            tasks[task_id]['message'] = 'Conversion completed! Starting download...'
            tasks[task_id]['download_url'] = f"/download/{task_id}"
            tasks[task_id]['completed_at'] = datetime.now().isoformat()
            tasks[task_id]['filename'] = final_mp3_filename
            tasks[task_id]['final_file_path'] = str(mp3_file)  # Store the actual file path
            tasks[task_id]['temp_dir'] = str(temp_dir)  # Store temp directory for cleanup
            logger.info(f"MP3 conversion successful: {mp3_file}")
        else:
            # MP3 conversion failed but we have the original audio file
            ext = original_file.suffix[1:]  # Get extension without dot
            logger.warning(f"MP3 conversion failed, using original {ext} file")
            
            # Rename original file to use sanitized title
            final_original_filename = f"{sanitized_title}.{ext}"
            final_original_file = temp_dir / final_original_filename
            
            # If file already exists, add a number suffix
            counter = 1
            while final_original_file.exists():
                final_original_filename = f"{sanitized_title} ({counter}).{ext}"
                final_original_file = temp_dir / final_original_filename
                counter += 1
            
            # Move original file to final name
            import shutil
            shutil.move(original_file, final_original_file)
            
            tasks[task_id]['status'] = 'completed'  # Mark as completed even though conversion failed
            tasks[task_id]['progress'] = 100.0
            tasks[task_id]['message'] = f'Download completed but conversion to MP3 failed. Original {ext.upper()} file available.'
            tasks[task_id]['download_url'] = f"/download/{task_id}"
            tasks[task_id]['completed_at'] = datetime.now().isoformat()
            tasks[task_id]['filename'] = final_original_filename
            tasks[task_id]['final_file_path'] = str(final_original_file)
            tasks[task_id]['temp_dir'] = str(temp_dir)  # Store temp directory for cleanup
            tasks[task_id]['error'] = "MP3 conversion failed, but original audio file is available"
            
    except Exception as e:
        logger.error(f"Download failed for task {task_id}: {str(e)}")
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['error'] = str(e)
        tasks[task_id]['message'] = f'Download failed: {str(e)}'
        
        # Clean up temp directory on failure
        try:
            temp_dir = Path(f"temp_{task_id}")
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up temp directory for task {task_id}: {str(cleanup_error)}")

async def download_video_mp4(task_id: str, url: str, quality: VideoQuality, start_time: int = None, end_time: int = None):
    """Background task to download video as MP4"""
    try:
        tasks[task_id]['status'] = 'processing'
        tasks[task_id]['message'] = 'Starting video download...'
        
        # Clean URL to remove playlist parameters
        clean_url = clean_youtube_url(url)
        logger.info(f"Original URL: {url}")
        logger.info(f"Cleaned URL: {clean_url}")
        
        # First get video info to store title early
        try:
            with yt_dlp.YoutubeDL({'no_warnings': True, 'noplaylist': True}) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                video_title = info.get('title', 'Unknown')
                tasks[task_id]['title'] = video_title
                logger.info(f"Video title: {video_title}")
        except Exception as e:
            logger.warning(f"Could not get video title: {str(e)}")
            video_title = 'Unknown'
        
        # Sanitize video title for filename
        def sanitize_filename(filename):
            # Remove invalid filename characters
            sanitized = re.sub(r'[\\/*?:"<>|]', '', filename)
            # Replace multiple spaces with single space
            sanitized = re.sub(r'\s+', ' ', sanitized)
            # Trim and limit length
            sanitized = sanitized.strip()[:100]  # Limit to 100 characters
            return sanitized if sanitized else 'Unknown'
        
        sanitized_title = sanitize_filename(video_title)
        
        # Create temporary directory for this task
        temp_dir = Path(f"temp_{task_id}")
        temp_dir.mkdir(exist_ok=True)
        
        # Create unique filename for temporary download
        output_filename = f"{task_id}.%(ext)s"
        output_path = str(temp_dir / output_filename)
        
        # Get video download options
        ydl_opts = get_video_ydl_opts(quality, output_path, start_time, end_time)
        ydl_opts['progress_hooks'] = [progress_hook]
        
        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Add task_id to the progress hook context
            ydl._progress_hooks[0] = lambda d: progress_hook({**d, 'task_id': task_id})
            ydl.download([clean_url])
        
        # Check for downloaded file (likely mp4, webm, or mkv)
        possible_extensions = ['mp4', 'webm', 'mkv', 'avi', 'mov']
        downloaded_file = None
        
        for ext in possible_extensions:
            potential_file = temp_dir / f"{task_id}.{ext}"
            if potential_file.exists():
                downloaded_file = potential_file
                logger.info(f"Found downloaded {ext.upper()} file: {potential_file}")
                break
        
        if not downloaded_file:
            raise Exception("Failed to download video file")
        
        # Create final MP4 filename with sanitized title
        final_filename = f"{sanitized_title}.mp4"
        final_file = temp_dir / final_filename
        
        # If file already exists, add a number suffix
        counter = 1
        while final_file.exists():
            final_filename = f"{sanitized_title} ({counter}).mp4"
            final_file = temp_dir / final_filename
            counter += 1
        
        # If the downloaded file is not MP4, try to convert it
        if downloaded_file.suffix.lower() != '.mp4':
            tasks[task_id]['message'] = 'Converting video to MP4...'
            logger.info(f"Converting {downloaded_file.suffix} to MP4...")
            
            try:
                # Use yt-dlp with FFmpeg to convert to MP4
                convert_opts = {
                    'outtmpl': str(final_file),
                    'format': 'best',
                    'postprocessors': [{
                        'key': 'FFmpegVideoConvertor',
                        'preferedformat': 'mp4',
                    }],
                    'no_warnings': True,
                }
                
                if ffmpeg_path:
                    convert_opts['ffmpeg_location'] = ffmpeg_path
                
                # Copy the downloaded file to a temporary location for conversion
                temp_input = temp_dir / f"temp_input_{task_id}{downloaded_file.suffix}"
                shutil.copy2(downloaded_file, temp_input)
                
                # Convert using FFmpeg through yt-dlp
                with yt_dlp.YoutubeDL(convert_opts) as ydl:
                    ydl.process_info({
                        'filepath': str(temp_input),
                        'ext': downloaded_file.suffix[1:],  # Remove the dot
                    })
                
                # Clean up temporary file
                if temp_input.exists():
                    temp_input.unlink()
                
                # Remove original downloaded file
                if downloaded_file.exists():
                    downloaded_file.unlink()
                    logger.info(f"Deleted original file: {downloaded_file}")
                
            except Exception as e:
                logger.warning(f"MP4 conversion failed, using original file: {str(e)}")
                # Just rename the original file
                shutil.move(downloaded_file, final_file)
        else:
            # File is already MP4, just rename it
            shutil.move(downloaded_file, final_file)
        
        # Update task status
        if final_file.exists():
            tasks[task_id]['status'] = 'completed'
            tasks[task_id]['progress'] = 100.0
            tasks[task_id]['message'] = 'Video download completed successfully'
            tasks[task_id]['download_url'] = f"/download/{task_id}"
            tasks[task_id]['completed_at'] = datetime.now().isoformat()
            tasks[task_id]['filename'] = final_filename
            tasks[task_id]['final_file_path'] = str(final_file)
            tasks[task_id]['temp_dir'] = str(temp_dir)  # Store temp directory for cleanup
            logger.info(f"Video download successful: {final_file}")
        else:
            raise Exception("Final video file not found after processing")
            
    except Exception as e:
        logger.error(f"Video download failed for task {task_id}: {str(e)}")
        tasks[task_id]['status'] = 'failed'
        tasks[task_id]['error'] = str(e)
        tasks[task_id]['message'] = f'Video download failed: {str(e)}'
        
        # Clean up temp directory on failure
        try:
            temp_dir = Path(f"temp_{task_id}")
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
        except Exception as cleanup_error:
            logger.error(f"Failed to clean up temp directory for task {task_id}: {str(cleanup_error)}")

# API Endpoints

@app.get("/api-info")
async def api_info():
    """API information endpoint"""
    return {
        "message": "YouTube to MP3 Converter API",
        "version": "1.0.0",
        "website": "Main website available at /",
        "endpoints": {
            "GET /": "Main website (index.html)",
            "GET /api": "API documentation page",
            "GET /contact": "Contact page",
            "GET /faqs": "FAQs page", 
            "GET /changelog": "Changelog page",
            "GET /api-info": "This API information",
            "POST /convert": "Convert YouTube video to MP3",
            "GET /video-info": "Get video information",
            "GET /task/{task_id}": "Get task status",
            "GET /download/{task_id}": "Download converted file",
            "POST /playlist": "Convert YouTube playlist to MP3",
            "GET /tasks": "List all tasks",
            "DELETE /task/{task_id}": "Delete task and file",
            "GET /qualities": "Get available audio qualities",
            "GET /check-ffmpeg": "Check if FFmpeg is installed on the server",
            "POST /set-ffmpeg-path": "Set the FFmpeg path for the application",
            "GET /ffmpeg-path": "Get the current FFmpeg path",
            "POST /download-multiple": "Download multiple files as a ZIP archive",
            "GET /check-mp3-conversion": "Check available MP3 conversion methods"
        }
    }

@app.get("/qualities")
async def get_qualities():
    """Get available audio and video qualities"""
    return {
        "audio_qualities": {
            "low": {"bitrate": "96kbps", "description": "Low quality, smaller file size"},
            "medium": {"bitrate": "128kbps", "description": "Medium quality, balanced"},
            "high": {"bitrate": "192kbps", "description": "High quality, larger file size"},
            "ultra": {"bitrate": "320kbps", "description": "Ultra quality, largest file size"}
        },
        "video_qualities": {
            "360p": {"resolution": "360p", "description": "Low quality, smaller file size"},
            "480p": {"resolution": "480p", "description": "Standard quality, balanced size"},
            "720p": {"resolution": "720p", "description": "High quality HD, larger file size"},
            "1080p": {"resolution": "1080p", "description": "Ultra quality Full HD, largest file size"},
            "best": {"resolution": "Best", "description": "Best available quality"}
        }
    }

@app.get("/video-info")
async def get_video_info(url: str = Query(..., description="YouTube video URL")):
    """Get video information without downloading"""
    try:
        ydl_opts = {
            'no_warnings': True, 
            'noplaylist': True,
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            'embed_subs': False,
            'age_limit': None,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            return VideoInfo(
                id=info.get('id', ''),
                title=info.get('title', 'Unknown'),
                duration=info.get('duration', 0),
                uploader=info.get('uploader', 'Unknown'),
                view_count=info.get('view_count', 0),
                upload_date=info.get('upload_date', ''),
                thumbnail=info.get('thumbnail', ''),
                description=info.get('description', '')[:500] + ('...' if len(info.get('description', '')) > 500 else '')
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to get video info: {str(e)}")

@app.post("/convert")
async def convert_video(request: DownloadRequest, background_tasks: BackgroundTasks, http_request: Request):
    """Convert YouTube video to MP3"""
    task_id = str(uuid.uuid4())
    session_id = get_session_id(http_request)
    
    logger.info(f"Creating new MP3 conversion task: {task_id} for URL: {request.url}")
    logger.info(f"Session ID: {session_id}")
    
    # Try to get video title early
    video_title = "Unknown"
    try:
        early_ydl_opts = {
            'no_warnings': True, 
            'noplaylist': True,
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            'embed_subs': False,
            'age_limit': None,
        }
        with yt_dlp.YoutubeDL(early_ydl_opts) as ydl:
            info = ydl.extract_info(str(request.url), download=False)
            video_title = info.get('title', 'Unknown')
    except Exception as e:
        logger.warning(f"Could not get video title early for task {task_id}: {str(e)}")
    
    # Initialize task
    try:
        tasks[task_id] = {
            'status': 'queued',
            'progress': 0.0,
            'message': 'Task queued',
            'created_at': datetime.now().isoformat(),
            'url': str(request.url),
            'quality': request.quality,
            'title': video_title,
            'session_id': session_id
        }
        
        logger.info(f"Task {task_id} initialized successfully")
        logger.info(f"Current tasks count: {len(tasks)}")
        
        # Associate task with session
        if session_id not in session_files:
            session_files[session_id] = []
        session_files[session_id].append(task_id)
        
        logger.info(f"Task {task_id} associated with session {session_id}")
        
        # Add background task
        background_tasks.add_task(
            download_video, 
            task_id, 
            str(request.url), 
            request.quality,
            request.start_time,
            request.end_time
        )
        
        logger.info(f"Background task started for {task_id}")
        
        return DownloadResponse(
            task_id=task_id,
            status="queued",
            message="Download task has been queued"
        )
        
    except Exception as e:
        logger.error(f"Failed to create task {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to create download task: {str(e)}")

@app.post("/convert-video")
async def convert_video_mp4(request: VideoDownloadRequest, background_tasks: BackgroundTasks, http_request: Request):
    """Convert YouTube video to MP4"""
    task_id = str(uuid.uuid4())
    session_id = get_session_id(http_request)
    
    # Try to get video title early
    video_title = "Unknown"
    try:
        early_ydl_opts = {
            'no_warnings': True, 
            'noplaylist': True,
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            'embed_subs': False,
            'age_limit': None,
        }
        with yt_dlp.YoutubeDL(early_ydl_opts) as ydl:
            info = ydl.extract_info(str(request.url), download=False)
            video_title = info.get('title', 'Unknown')
    except Exception as e:
        logger.warning(f"Could not get video title early: {str(e)}")
    
    # Initialize task
    tasks[task_id] = {
        'status': 'queued',
        'progress': 0.0,
        'message': 'Video task queued',
        'created_at': datetime.now().isoformat(),
        'url': str(request.url),
        'quality': request.quality,
        'title': video_title,
        'type': 'video',  # Mark as video task
        'session_id': session_id
    }
    
    # Associate task with session
    session_files[session_id].append(task_id)
    
    # Add background task
    background_tasks.add_task(
        download_video_mp4, 
        task_id, 
        str(request.url), 
        request.quality,
        request.start_time,
        request.end_time
    )
    
    return DownloadResponse(
        task_id=task_id,
        status="queued",
        message="Video download task has been queued"
    )

@app.post("/contact")
async def submit_contact_form(contact_data: ContactForm, background_tasks: BackgroundTasks):
    """Submit contact form and send email"""
    try:
        logger.info(f"Received contact form submission from: {contact_data.email}")
        
        # Add email sending as background task
        background_tasks.add_task(send_contact_email, contact_data)
        
        return {
            "success": True,
            "message": "Thank you for your message! We'll get back to you within 24 hours."
        }
    except Exception as e:
        logger.error(f"Contact form submission failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to submit contact form")

@app.post("/contact/test")
async def test_contact_form(data: dict):
    """Test endpoint to debug contact form data"""
    logger.info(f"Raw contact form data received: {data}")
    
    # Try to validate manually
    try:
        contact_data = ContactForm(**data)
        return {
            "success": True,
            "message": "Validation successful",
            "data": contact_data.dict()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": data
        }

@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """Get task status"""
    logger.info(f"Status check requested for task: {task_id}")
    logger.info(f"Current tasks in memory: {list(tasks.keys())}")
    logger.info(f"Total tasks count: {len(tasks)}")
    
    if task_id not in tasks:
        logger.error(f"Task {task_id} not found in tasks dictionary")
        logger.info(f"Available tasks: {list(tasks.keys())}")
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    logger.info(f"Task {task_id} found with status: {task['status']}")
    
    return TaskStatus(
        task_id=task_id,
        status=task['status'],
        progress=task['progress'],
        message=task['message'],
        download_url=task.get('download_url'),
        error=task.get('error'),
        created_at=task['created_at'],
        completed_at=task.get('completed_at')
    )

@app.get("/download/{task_id}")
async def download_file(task_id: str):
    """Download the converted MP3 file or original audio file"""
    # Check if task exists in active tasks
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    # Check if we have the final file path stored
    if 'final_file_path' in task:
        file_path = Path(task['final_file_path'])
        if file_path.exists():
            filename = task.get('filename', file_path.name)
            
            # Determine media type based on extension
            media_type = 'audio/mpeg'
            if file_path.suffix == '.m4a':
                media_type = 'audio/mp4'
            elif file_path.suffix == '.webm':
                media_type = 'audio/webm'
            elif file_path.suffix == '.mp4':
                media_type = 'video/mp4'
            elif file_path.suffix == '.mkv':
                media_type = 'video/x-matroska'
            elif file_path.suffix == '.avi':
                media_type = 'video/x-msvideo'
            
            # Log the filename for debugging
            logger.info(f"Serving file with filename: {filename}")
            
            return FileResponse(
                path=str(file_path),
                filename=filename,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'}
            )
    
    # Fallback: Check for different possible file extensions with task_id
    file_extensions = ['mp3', 'm4a', 'webm', 'mp4', 'mkv', 'avi']
    file_path = None
    
    for ext in file_extensions:
        potential_path = downloads_dir / f"{task_id}.{ext}"
        if potential_path.exists():
            file_path = potential_path
            break
    
    # If still not found, try to find any file that starts with the task_id
    if not file_path:
        for file in downloads_dir.glob(f"{task_id}.*"):
            file_path = file
            break
    
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")
    
    # Use the filename from task or fallback to task_id with correct extension
    filename = task.get('filename', f"{task_id}{file_path.suffix}")
    
    # If the task has a title but no filename, use the title
    if 'title' in task and not task.get('filename'):
        title = task['title']
        # Sanitize title for use as filename
        title = re.sub(r'[\\/*?:"<>|]', '', title)  # Remove invalid filename chars
        filename = f"{title}{file_path.suffix}"
    
    # Determine media type based on extension
    media_type = 'audio/mpeg'
    if file_path.suffix == '.m4a':
        media_type = 'audio/mp4'
    elif file_path.suffix == '.webm':
        media_type = 'audio/webm'
    elif file_path.suffix == '.mp4':
        media_type = 'video/mp4'
    elif file_path.suffix == '.mkv':
        media_type = 'video/x-matroska'
    elif file_path.suffix == '.avi':
        media_type = 'video/x-msvideo'
    
    # Log the filename for debugging
    logger.info(f"Serving fallback file with filename: {filename}")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )



@app.post("/playlist")
async def convert_playlist(request: PlaylistRequest, background_tasks: BackgroundTasks):
    """Convert YouTube playlist to MP3 files"""
    try:
        ydl_opts = {'no_warnings': True, 'extract_flat': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(str(request.url), download=False)
            
            if 'entries' not in playlist_info:
                raise HTTPException(status_code=400, detail="Invalid playlist URL")
            
            entries = playlist_info['entries'][:request.max_videos]
            task_ids = []
            
            for entry in entries:
                if entry and entry.get('url'):
                    task_id = str(uuid.uuid4())
                    
                    tasks[task_id] = {
                        'status': 'queued',
                        'progress': 0.0,
                        'message': 'Task queued',
                        'created_at': datetime.now().isoformat(),
                        'url': entry['url'],
                        'quality': request.quality,
                        'title': entry.get('title', 'Unknown')
                    }
                    
                    background_tasks.add_task(
                        download_video,
                        task_id,
                        entry['url'],
                        request.quality
                    )
                    
                    task_ids.append(task_id)
            
            return {
                "message": f"Queued {len(task_ids)} videos for conversion",
                "task_ids": task_ids,
                "playlist_title": playlist_info.get('title', 'Unknown Playlist')
            }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to process playlist: {str(e)}")

@app.get("/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = Query(50, le=100)):
    """List all tasks with optional status filter"""
    filtered_tasks = []
    
    for task_id, task in tasks.items():
        if status is None or task['status'] == status:
            filtered_tasks.append({
                'task_id': task_id,
                **task
            })
    
    # Sort by creation time, newest first
    filtered_tasks.sort(key=lambda x: x['created_at'], reverse=True)
    
    return {
        "tasks": filtered_tasks[:limit],
        "total": len(filtered_tasks)
    }

@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """Delete task and associated file"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    # Delete the temp directory if it exists
    if 'temp_dir' in task:
        temp_dir_path = Path(task['temp_dir'])
        if temp_dir_path.exists():
            try:
                shutil.rmtree(temp_dir_path)
                logger.info(f"Deleted temp directory: {temp_dir_path}")
            except Exception as e:
                logger.warning(f"Could not delete temp directory {temp_dir_path}: {str(e)}")
    
    # Fallback: Delete the actual file if it exists (for old tasks)
    if 'final_file_path' in task:
        file_path = Path(task['final_file_path'])
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted file: {file_path}")
            except Exception as e:
                logger.warning(f"Could not delete file {file_path}: {str(e)}")
    
    # Fallback: try to delete files with task_id pattern (for old tasks)
    for ext in ['mp3', 'm4a', 'webm', 'mp4', 'mkv', 'avi']:
        file_path = downloads_dir / f"{task_id}.{ext}"
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted fallback file: {file_path}")
            except Exception as e:
                logger.warning(f"Could not delete fallback file {file_path}: {str(e)}")
    
    # Remove task from memory
    del tasks[task_id]
    
    return {"message": "Task and file deleted successfully"}

@app.post("/cleanup")
async def cleanup_old_files(days: int = Query(7, description="Delete files older than this many days")):
    """Clean up old files and tasks"""
    cutoff_date = datetime.now() - timedelta(days=days)
    deleted_count = 0
    
    # Clean up old temp directories
    for temp_dir in Path(".").glob("temp_*"):
        if temp_dir.is_dir():
            try:
                # Check if directory is old enough
                if temp_dir.stat().st_mtime < cutoff_date.timestamp():
                    shutil.rmtree(temp_dir)
                    deleted_count += 1
                    logger.info(f"Cleaned up old temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"Could not clean up temp directory {temp_dir}: {str(e)}")
    
    # Clean up old files in downloads directory (for backwards compatibility)
    for pattern in ["*.mp3", "*.m4a", "*.webm", "*.mp4", "*.mkv", "*.avi"]:
        for file_path in downloads_dir.glob(pattern):
            if file_path.stat().st_mtime < cutoff_date.timestamp():
                file_path.unlink()
                deleted_count += 1
    
    # Clean up tasks (including those with deleted temp directories)
    tasks_to_delete = []
    for task_id, task in tasks.items():
        created_at = datetime.fromisoformat(task['created_at'])
        if created_at < cutoff_date:
            tasks_to_delete.append(task_id)
        # Also remove tasks whose temp directories no longer exist
        elif 'temp_dir' in task:
            temp_dir_path = Path(task['temp_dir'])
            if not temp_dir_path.exists():
                tasks_to_delete.append(task_id)
    
    for task_id in tasks_to_delete:
        del tasks[task_id]
    
    return {
        "message": f"Cleanup completed",
        "files_deleted": deleted_count,
        "tasks_deleted": len(tasks_to_delete)
    }

@app.get("/search")
async def search_youtube(query: str = Query(..., description="Search query"), max_results: int = Query(10, le=50)):
    """Search YouTube videos"""
    try:
        ydl_opts = {
            'no_warnings': True,
            'extract_flat': True,
            'default_search': 'ytsearch' + str(max_results) + ':',
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            'embed_subs': False,
            'age_limit': None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(query, download=False)
            
            videos = []
            for entry in search_results.get('entries', []):
                if entry:
                    videos.append({
                        'id': entry.get('id'),
                        'title': entry.get('title'),
                        'url': entry.get('url'),
                        'duration': entry.get('duration'),
                        'uploader': entry.get('uploader'),
                        'view_count': entry.get('view_count'),
                        'thumbnail': entry.get('thumbnail')
                    })
            
            return {
                "query": query,
                "results": videos,
                "total": len(videos)
            }
            
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Search failed: {str(e)}")

@app.get("/check-ffmpeg")
async def check_ffmpeg():
    """Check if FFmpeg is installed on the server"""
    try:
        # Try to run ffmpeg -version
        import subprocess
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version = result.stdout.split('\n')[0] if result.stdout else "Unknown version"
            return {"installed": True, "version": version}
        else:
            return {"installed": False, "error": "FFmpeg command returned non-zero exit code"}
    except Exception as e:
        return {"installed": False, "error": str(e)}

@app.post("/set-ffmpeg-path")
async def set_ffmpeg_path(path: str = Query(..., description="Path to FFmpeg executable")):
    """Set the FFmpeg path for the application"""
    global ffmpeg_path
    
    # Validate the path
    try:
        import subprocess
        path = path.strip()
        
        # Try to run ffmpeg -version with the provided path
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5)
        
        if result.returncode == 0:
            ffmpeg_path = path
            return {"success": True, "message": "FFmpeg path set successfully", "version": result.stdout.split('\n')[0]}
        else:
            return {"success": False, "message": "Invalid FFmpeg path", "error": result.stderr}
    except Exception as e:
        return {"success": False, "message": "Failed to set FFmpeg path", "error": str(e)}

@app.get("/ffmpeg-path")
async def get_ffmpeg_path():
    """Get the current FFmpeg path"""
    return {"path": ffmpeg_path}

@app.post("/download-multiple")
async def download_multiple_files(task_ids: List[str]):
    """Download multiple files as a ZIP archive"""
    # Check if all task IDs exist
    valid_tasks = []
    for task_id in task_ids:
        if task_id in tasks and tasks[task_id]['status'] == 'completed':
            valid_tasks.append(task_id)
    
    if not valid_tasks:
        raise HTTPException(status_code=400, detail="No valid completed tasks found")
    
    # Create a ZIP file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for task_id in valid_tasks:
            task = tasks[task_id]
            
            # Find the file - first check if we have the final file path
            file_path = None
            if 'final_file_path' in task:
                file_path = Path(task['final_file_path'])
                if not file_path.exists():
                    file_path = None
            
            # Fallback: check downloads directory for old tasks
            if not file_path:
                for ext in ['mp3', 'm4a', 'webm']:
                    potential_path = downloads_dir / f"{task_id}.{ext}"
                    if potential_path.exists():
                        file_path = potential_path
                        break
            
            if not file_path:
                continue
            
            # Use the filename from task or fallback
            filename = task.get('filename', f"{task_id}{file_path.suffix}")
            
            # Sanitize filename
            filename = re.sub(r'[\\/*?:"<>|]', '', filename)
            
            # Add file to ZIP
            zip_file.write(file_path, filename)
    
    # Reset buffer position
    zip_buffer.seek(0)
    
    # Return the ZIP file
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=youtube_downloads.zip"
        }
    )

@app.get("/check-mp3-conversion")
async def check_mp3_conversion():
    """Check available MP3 conversion methods"""
    ffmpeg_available = False
    
    # Check FFmpeg
    try:
        import subprocess
        if ffmpeg_path:
            result = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=5)
            ffmpeg_available = result.returncode == 0
        else:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            ffmpeg_available = result.returncode == 0
    except:
        ffmpeg_available = False
    
    return {
        "ffmpeg_available": ffmpeg_available,
        "pure_python_available": PURE_PYTHON_MP3_AVAILABLE,
        "ffmpeg_path": ffmpeg_path,
        "conversion_method": "FFmpeg" if ffmpeg_available else "Pure Python" if PURE_PYTHON_MP3_AVAILABLE else "None"
    }

@app.post("/test-download")
async def test_download(url: str = Query(..., description="YouTube URL to test")):
    """Test download without conversion for debugging"""
    try:
        # Clean the URL
        clean_url = clean_youtube_url(url)
        logger.info(f"Testing download for URL: {clean_url}")
        
        # Test with yt-dlp info extraction
        test_ydl_opts = {
            'no_warnings': True, 
            'noplaylist': True,
            # Add user agent and other headers to bypass bot detection
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.7',
                'Keep-Alive': '115',
                'Connection': 'keep-alive',
            },
            # Additional options to bypass restrictions
            'extractor_args': {
                'youtube': {
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs'],
                }
            },
            'embed_subs': False,
            'age_limit': None,
        }
        with yt_dlp.YoutubeDL(test_ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            
        return {
            "success": True,
            "original_url": url,
            "cleaned_url": clean_url,
            "video_info": {
                "title": info.get('title', 'Unknown'),
                "duration": info.get('duration', 0),
                "uploader": info.get('uploader', 'Unknown'),
                "formats_available": len(info.get('formats', [])),
                "has_audio": any('audio' in str(f.get('acodec', '')).lower() for f in info.get('formats', [])),
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "original_url": url,
            "cleaned_url": clean_youtube_url(url) if url else None
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)