"""``python -m training``: run the training pipeline and exit with its status."""

import sys

from training.pipeline import main

sys.exit(main())
