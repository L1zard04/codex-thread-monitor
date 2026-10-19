from pathlib import Path
import tempfile
import unittest

from configure import write_runtime


class ConfigureTests(unittest.TestCase):
    def test_runtime_is_unicode_and_points_to_windowed_python(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            python=folder/'python.exe'
            pythonw=folder/'pythonw.exe'
            pythonw.touch()
            target=folder/'python-path.txt'
            write_runtime(python,target)
            self.assertEqual(target.read_text(encoding='utf-16'),str(pythonw)+'\n')

    def test_missing_windowed_python_does_not_write_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            target=folder/'python-path.txt'
            with self.assertRaises(FileNotFoundError):
                write_runtime(folder/'python.exe',target)
            self.assertFalse(target.exists())
