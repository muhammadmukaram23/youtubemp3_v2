#!/usr/bin/env python3
"""
YouTube to MP3 Converter Server Startup Script
This script starts the FastAPI server with the website integrated
"""

import uvicorn
import sys
import os

def main():
    """Start the server with optimal settings"""
    print("🚀 Starting YouTube to MP3 Converter Server...")
    print("📁 Serving website and API from the same server")
    print("🌐 Website will be available at: http://localhost:8000")
    print("🔧 API endpoints available at: http://localhost:8000/api-info")
    print("=" * 60)
    
    try:
        # Start the server
        uvicorn.run(
            "main:app",
            host="0.0.0.0",  # Allow access from other devices on network
            port=8000,
            reload=True,     # Auto-reload on code changes
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 