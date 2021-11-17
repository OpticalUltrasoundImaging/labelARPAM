# Copyright (c) 2016 Tzutalin
# Create by TzuTaLin <tzu.ta.lin@gmail.com>

from PyQt5.QtGui import QImage

from typing import Optional
from enum import Enum
import os.path

from arpamutils.roi import ROI_File, CoImageSet
from arpamutils.metadata import ImgMeta


class LabelFileFormat(Enum):
    ARPAM = 4


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
        self.arpam_roi_file: Optional[ROI_File] = None
        self.arpam_img_meta: Optional[ImgMeta] = None
        self.arpam_img_set: Optional[CoImageSet] = None
        self.filename = filename

        if arpam:
            self._load_arpam_roi_file()

    def _load_arpam_roi_file(self):
        self.arpam_img_set = CoImageSet.from_path(self.filename)

        ## Load ROI file
        self.arpam_roi_file = ROI_File.from_img_path(self.filename)
        for bbox in self.arpam_roi_file.bboxes:
            x_max = round(bbox.xmax * self.arpam_roi_file.size.w)
            x_min = round(bbox.xmin * self.arpam_roi_file.size.w)
            y_max = round(bbox.ymax * self.arpam_roi_file.size.h)
            y_min = round(bbox.ymin * self.arpam_roi_file.size.h)

            points = [
                (x_min, y_min),
                (x_max, y_min),
                (x_max, y_max),
                (x_min, y_max),
            ]

            shape = (bbox.name, points, None, None)
            self.shapes.append(shape)

        ## Load meta file
        meta_path = self.arpam_img_set.meta
        # If meta file not found, silently ignore
        if meta_path.exists():
            self.arpam_img_meta = ImgMeta.from_path(self.arpam_roi_file.img_set.meta)

    def save_arpam_format(self, shapes, image_path, image_data):
        if isinstance(image_data, QImage):
            image = image_data
        else:
            image = QImage()
            image.load(image_path)

        _size = image.size()
        h, w = _size.height(), _size.width()
        self.arpam_roi_file.clear_bboxes()
        for shape in shapes:
            points = shape["points"]
            x = [p[0] for p in points]
            y = [p[1] for p in points]
            print(points)
            print(
                min(x) / w,
                max(x) / w,                
                min(y) / h,
                max(y) / h,
            )
            self.arpam_roi_file.add_bbox(
                label=shape["label"],
                xmin=min(x) / w,
                xmax=max(x) / w,
                ymin=min(y) / h,
                ymax=max(y) / h,
            )

        self.arpam_roi_file.save()
        self._load_arpam_roi_file()

    def toggle_verify(self):
        self.verified = not self.verified

    @staticmethod
    def is_label_file(filename):
        file_suffix = os.path.splitext(filename)[1].lower()
        return file_suffix == LabelFile.suffix
