"""Configuration module for the on-chain metrics fetcher."""

import os
from dotenv import load_dotenv

load_dotenv()

GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
GLASSNODE_BASE_URL = "https://api.glassnode.com"

# Default asset
ASSET = "BTC"
