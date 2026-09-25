"""Electronic scale → system, through a small adapter.

Today the punkt operator types the weight (adapter 'manual'). The scale model, port and protocol are not known
yet, so nothing is faked here: no adapter pretends to read a weight it doesn't have.

To connect a real scale later:
  1. write a class with ``name`` and ``read() -> float`` (kg, stable reading) — e.g. serial/RS-232 or a small
     HTTP bridge next to the scale;
  2. register it in ADAPTERS;
  3. set the 'scale_adapter' setting to its name.
The punkt screen then shows a “Tarozidan olish” button that fills the kg field; the operator still confirms,
and the receipt is stored exactly as a typed one (audit shows the source).
"""
from .settings import get_setting
from .utils import UserError


class ManualScale:
    """No device: the operator types the kg shown on the scale display."""
    name = 'manual'
    connected = False

    def read(self):
        raise UserError('Elektron tarozi ulanmagan — kg ni tarozi ekranidan qo‘lda kiriting.')


ADAPTERS = {'manual': ManualScale}


def get_adapter():
    name = (get_setting('scale_adapter') or 'manual').strip()
    return ADAPTERS.get(name, ManualScale)()


def scale_reading(probe=False):
    """probe=True: only report whether a device is connected (for the page). Otherwise read a weight."""
    adapter = get_adapter()
    if probe:
        return {'adapter': adapter.name, 'connected': adapter.connected}
    return {'adapter': adapter.name, 'connected': adapter.connected, 'kg': adapter.read()}
