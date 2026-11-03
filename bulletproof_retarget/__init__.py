"""Bulletproof Retarget - retarget animation between humanoid rigs.

Panel: 3D Viewport > Sidebar (N) > BP Retarget
"""

from . import control_rig, ops, props, standards, ui

_modules = (props, ops, ui)


def register():
    standards.register()
    control_rig.register()
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
    control_rig.unregister()
    standards.unregister()
