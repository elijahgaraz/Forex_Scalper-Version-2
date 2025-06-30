# main.py
import sys
import os

# Add the project root to sys.path to allow absolute imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from gui import MainApplication
from settings import Settings

def main() -> None:
    settings = Settings.load()
    app = MainApplication(settings)
    app.mainloop()

if __name__ == "__main__":
    main()
