"""Render a DXF to a one-page vector PDF (ezdxf + matplotlib), black on white, the way Stoa's Swiss
drawings come as a PDF next to a DXF twin. Derived file, not part of the original download.

    python tools/unseen_dxf_to_pdf.py in.dxf out.pdf
"""
import sys

import ezdxf
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ezdxf.addons.drawing import Frontend, RenderContext, config
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

doc = ezdxf.readfile(sys.argv[1])
fig = plt.figure(figsize=(16.5, 11.7))
ax = fig.add_axes([0, 0, 1, 1])
cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                           color_policy=config.ColorPolicy.BLACK)
Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace())
fig.savefig(sys.argv[2], format="pdf")
