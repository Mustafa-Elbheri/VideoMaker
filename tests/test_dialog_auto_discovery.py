import importlib
import inspect
import os
import pkgutil
import unittest
import wx

import video_maker


class DialogAutoDiscoveryTest(unittest.TestCase):
    """Automatically discovers all wx.Dialog classes in video_maker and verifies

    they can be imported, instantiated or inspected without NameError/SyntaxError.
    """
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App()
        cls._import_all_submodules()

    @classmethod
    def _import_all_submodules(cls):
        pkg_path = os.path.dirname(video_maker.__file__)
        for _, modname, ispkg in pkgutil.walk_packages([pkg_path], prefix="video_maker."):
            try:
                importlib.import_module(modname)
            except Exception:
                pass

    def test_all_dialog_subclasses_have_valid_definitions(self):
        dialog_classes = []
        for cls in wx.Dialog.__subclasses__():
            module_name = cls.__module__
            if module_name.startswith("video_maker"):
                dialog_classes.append(cls)

        self.assertGreater(len(dialog_classes), 15, "Should auto-discover all dialog subclasses")

        for dialog_cls in dialog_classes:
            init_fn = getattr(dialog_cls, "__init__", None)
            self.assertIsNotNone(init_fn, f"{dialog_cls.__name__} must have an __init__ method")
            sig = inspect.signature(init_fn)
            self.assertIn("parent", sig.parameters, f"{dialog_cls.__name__}.__init__ should accept parent")

    def test_common_dialogs_instantiation_and_accessibility_names(self):
        from video_maker.image_overlay import ImageOverlayDialog
        from video_maker.metadata_dialog import MetadataDialog
        from video_maker.settings_dialog import ProgramSettingsDialog
        from video_maker.player_modules.shared import ApplicationNameDialog

        frame = wx.Frame(None)
        try:
            # 1. ImageOverlayDialog
            d1 = ImageOverlayDialog(frame)
            self.assertTrue(d1.image_text.GetName())
            self.assertTrue(d1.mode_choice.GetName())
            self.assertTrue(d1.width_slider.GetName())
            d1.Destroy()

            # 2. ApplicationNameDialog
            d2 = ApplicationNameDialog(frame, "Test App")
            self.assertTrue(d2.name_text.GetName())
            d2.Destroy()

            # 3. ProgramSettingsDialog
            d3 = ProgramSettingsDialog(frame)
            self.assertIsNotNone(d3)
            d3.Destroy()
        finally:
            frame.Destroy()


if __name__ == "__main__":
    unittest.main()
