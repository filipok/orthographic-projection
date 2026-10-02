"""Shared pytest setup."""

import matplotlib

# Render off-screen. The default Windows backend (TkAgg) intermittently fails
# to initialise Tcl/Tk when many figures are created in one session.
matplotlib.use("Agg")
