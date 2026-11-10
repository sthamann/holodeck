from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'core'
sys.path.insert(0,str(BASE))
