# Copyright (c) 2016 Tzutalin
# Create by TzuTaLin <tzu.ta.lin@gmail.com>

from PyQt5.QtGui import QImage

from typing import Optional
from enum import Enum
import os.path

from arpamutils import roi as arpam_roi


class LabelFileFormat(Enum):
    ARPAM = 4


class CoImageType(Enum):
    NOT_SET = 0
    PA = 1
    US = 2
    SUM = 3
    DEBUG = 4


class LabelFileError(Exception):
    pass


class LabelFile(object):
    # It might be changed as window creates. By default, using XML ext
    suffix = ".json"

    def __init__(self, filename=None, arpam=False):
        self.shapes = []
        self.image_path = None
        self.image_data = None
        self.verified = False
        self.arpam_roi_file: Optional[arpam_roi.ROI_File] = None
        if arpam:
            self.arpam_roi_file = arpam_roi.ROI_File.from_img_path(filename)
            for bbox in self.arpam_roi_file.bboxes:
                x_max = round(bbox.xmax * self.arpam_roi_file.size.w)
                x_min = round(bbox.xmin * self.arpam_roi_file.size.w)
                y_max = round(bbox.ymax * self.arpam_roi_file.size.w)
                y_min = round(bbox.ymin * self.arpam_roi_file.size.w)

                points = [
                    (x_min, y_min),
                    (x_max, y_min),
                    (x_max, y_max),
                    (x_min, y_max),
                ]

                shape = (bbox.name, points, None, None)
                self.shapes.append(shape)

    def save_arpam_format(self, shapes, image_path, image_data):
        if isinstance(image_data, QImage):
            image = image_data
        else:
            image = QImage()
            image.load(image_path)

        self.arpam_roi_file.bboxes = []
        for shape in shapes:
            points = shape["points"]
            x = [p[0] for p in points]
            y = [p[1] for p in points]
            self.arpam_roi_file.add_bbox(shape["label"], min(x), max(x), min(y), max(y))

        self.arpam_roi_file.save()

    def toggle_verify(self):
        self.verified = not self.verified

    @staticmethod
    def is_label_file(filename):
        file_suffix = os.path.splitext(filename)[1].lower()
        return file_suffix == LabelFile.suffix
