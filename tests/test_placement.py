import unittest
from placement import restore_bounds


class PlacementTests(unittest.TestCase):
    def test_keeps_window_on_secondary_monitor_with_negative_coordinates(self):
        bounds={'width':500,'height':600,'x':-1000,'y':50}
        self.assertEqual(restore_bounds(bounds,[(0,0,1920,1040),(-1280,0,0,1024)]),bounds)

    def test_unplugged_monitor_and_oversized_window_are_clamped(self):
        bounds=restore_bounds({'width':4000,'height':3000,'x':5000,'y':2000},[(0,0,1280,720)])
        self.assertEqual(bounds,{'width':1280,'height':720,'x':0,'y':0})

    def test_corrupt_optional_geometry_falls_back(self):
        for value in [None,'bad',{}, {'width':True,'height':500,'x':0,'y':0}]:
            bounds=restore_bounds(value,[(0,0,1920,1040)])
            self.assertEqual((bounds['width'],bounds['height']),(360,420))

    def test_native_frame_and_titlebar_fit_inside_work_area(self):
        bounds=restore_bounds({'width':4000,'height':3000,'x':5000,'y':2000},
                              [(0,0,1920,1032)],frame_size=(16,39))
        self.assertEqual(bounds,{'width':1904,'height':993,'x':0,'y':0})
        edge=restore_bounds({'width':500,'height':600,'x':1800,'y':900},
                            [(0,0,1920,1032)],frame_size=(16,39))
        self.assertLessEqual(edge['x']+edge['width']+16,1920)
        self.assertLessEqual(edge['y']+edge['height']+39,1032)
