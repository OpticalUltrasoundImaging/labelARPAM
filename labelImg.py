#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pathlib import Path
from typing import List, Optional
from functools import partial
import argparse
import codecs
import os.path
import platform
import sys

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from libs.combobox import ComboBox
from libs.resources import *
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.labelDialog import LabelDialog
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError, LabelFileFormat
from libs.toolBar import ToolBar
from libs.hashableQListWidgetItem import HashableQListWidgetItem

from arpamutils import roi as arpam_roi
from arpamutils import metadata as arpam_meta
from arpamutils.roi import CoImageType

__appname__ = "labelARPAM"


class WindowMixin(object):
    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            add_actions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName("%sToolBar" % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            add_actions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(
        self,
        default_filename=None,
        default_prefdef_class_file=None,
        default_save_dir=None,
    ):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        self.os_name = platform.system()

        # Load string bundle for i18n
        self.string_bundle = StringBundle.get_bundle()
        get_str = lambda str_id: self.string_bundle.get_string(str_id)

        self.default_save_dir = default_save_dir
        self.label_file_format = LabelFileFormat.ARPAM
        self.label_file: Optional[LabelFile] = None

        self.arpam_img_type: CoImageType = CoImageType.UNKNOWN

        # For loading all image under a directory
        self.m_img_list: List[str] = []  # active list
        self.m_img_list_all: List[str] = []  # all images
        self.m_img_list_filtered: List[str] = []  # filtered images
        self.dir_name = None
        self.label_hist = []
        self.last_open_dir = None
        self.cur_img_idx: int = 0

        # Whether we need to save or not.
        self.dirty = False

        self._no_selection_slot = False
        self._beginner = False
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        # Load predefined classes to the list
        self.load_predefined_classes(default_prefdef_class_file)

        # Main widgets and related state.
        self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)

        self.items_to_shapes = {}
        self.shapes_to_items = {}
        self.prev_label_text = ""

        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 0)

        # Create a widget for using default label
        self.use_default_label_checkbox = QCheckBox(get_str("useDefaultLabel"))
        self.use_default_label_checkbox.setChecked(False)
        self.default_label_text_line = QLineEdit("")
        use_default_label_qhbox_layout = QHBoxLayout()
        use_default_label_qhbox_layout.addWidget(self.use_default_label_checkbox)
        use_default_label_qhbox_layout.addWidget(self.default_label_text_line)
        use_default_label_container = QWidget()
        use_default_label_container.setLayout(use_default_label_qhbox_layout)

        # Create a widget for edit
        self.edit_button = QToolButton()
        self.edit_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        # Add some of widgets to list_layout
        list_layout.addWidget(self.edit_button)
        list_layout.addWidget(use_default_label_container)

        # Create and add combobox for showing unique labels in group
        self.combo_box = ComboBox(self)
        list_layout.addWidget(self.combo_box)

        # Create and add a widget for showing current label items
        self.label_list = QListWidget()
        label_list_container = QWidget()
        label_list_container.setLayout(list_layout)
        self.label_list.itemActivated.connect(self.label_selection_changed)
        self.label_list.itemSelectionChanged.connect(self.label_selection_changed)
        self.label_list.itemDoubleClicked.connect(self.edit_label)
        # Connect to itemChanged to detect checkbox changes.
        self.label_list.itemChanged.connect(self.label_item_changed)
        list_layout.addWidget(self.label_list)

        self.dock = QDockWidget(get_str("boxLabelText"), self)
        self.dock.setObjectName(get_str("labels"))
        self.dock.setWidget(label_list_container)

        # Create a widget for picking only good images
        self._filter_thresh: float = float("-inf")
        self._last_filter_checked: bool = False

        def _filter_update_callback():
            if self.filter_checkbox.isChecked() == self._last_filter_checked:
                return

            self._last_filter_checked = self.filter_checkbox.isChecked()
            self._filter_thresh = float(self.filter_input.text())

            self._update_filtered_img_list()
            self.file_path = None
            self.open_next_image()
            self._update_QList_files()

        self.filter_checkbox = QCheckBox("Filter mean_ratio: ")
        self.filter_checkbox.setChecked(self._last_filter_checked)
        self.filter_checkbox.toggled.connect(_filter_update_callback)
        self.filter_input = QLineEdit("1.5")
        self.filter_input.setValidator(QDoubleValidator())
        self.filter_input.returnPressed.connect(_filter_update_callback)

        filter_qhbox_layout = QHBoxLayout()
        filter_qhbox_layout.addWidget(self.filter_checkbox)
        filter_qhbox_layout.addWidget(self.filter_input)

        filter_container = QWidget()
        filter_container.setLayout(filter_qhbox_layout)

        ### Image quality dock
        self.img_meta_dock = QDockWidget("Image Metadata", self)

        self.img_meta_label = QLabel()
        self.img_meta_label.setText("N/A")
        self.img_meta_dock.setObjectName("Image quality")

        self.img_meta_dock.setWidget(self.img_meta_label)

        ### File list widget
        self.file_list_widget = QListWidget()
        self.file_list_widget.itemDoubleClicked.connect(self.file_item_double_clicked)
        ## TODO impl filter

        file_list_layout = QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.addWidget(filter_container)
        file_list_layout.addWidget(self.file_list_widget)

        file_list_container = QWidget()
        file_list_container.setLayout(file_list_layout)
        self.file_dock = QDockWidget(get_str("fileList"), self)
        self.file_dock.setObjectName(get_str("files"))
        self.file_dock.setWidget(file_list_container)

        self.zoom_widget = ZoomWidget()
        self.color_dialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoom_request)
        self.canvas.set_drawing_shape_to_square(
            settings.get(SETTING_DRAW_SQUARE, False)
        )

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scroll_bars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar(),
        }
        self.scroll_area = scroll
        self.canvas.scrollRequest.connect(self.scroll_request)

        self.canvas.newShape.connect(self.new_shape)
        self.canvas.shapeMoved.connect(self.set_dirty)
        self.canvas.selectionChanged.connect(self.shape_selection_changed)
        self.canvas.drawingPolygon.connect(self.toggle_drawing_sensitive)

        self.setCentralWidget(scroll)

        self.addDockWidget(Qt.RightDockWidgetArea, self.img_meta_dock)
        self.img_meta_dock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)

        self.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)
        self.file_dock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.dock_features = (
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        )
        self.dock.setFeatures(self.dock.features() ^ self.dock_features)

        # Actions
        action = partial(new_action, self)
        quit = action(get_str("quit"), self.close, "Ctrl+Q", "quit", get_str("quitApp"))

        open = action(
            get_str("openFile"),
            self.open_file,
            "Ctrl+O",
            "open",
            get_str("openFileDetail"),
        )

        open_dir = action(
            get_str("openDir"),
            self.open_dir_dialog,
            "Ctrl+u",
            "open",
            get_str("openDir"),
        )

        # change_save_dir = action(get_str('changeSaveDir'), self.change_save_dir_dialog,
        # 'Ctrl+r', 'open', get_str('changeSavedAnnotationDir'))

        open_annotation = action(
            get_str("openAnnotation"),
            self.open_annotation_dialog,
            "Ctrl+Shift+O",
            "open",
            get_str("openAnnotationDetail"),
        )

        copy_prev_bounding = action(
            get_str("copyPrevBounding"),
            self.copy_previous_bounding_boxes,
            "Ctrl+v",
            "copy",
            get_str("copyPrevBounding"),
        )

        open_US_img = action(
            "US image",
            partial(self.action_open_coreg_img, CoImageType.US),
            "u",
            "open US img",
            "US image (u)",
        )
        open_PA_img = action(
            "PA image",
            partial(self.action_open_coreg_img, CoImageType.PA),
            "p",
            "open PA img",
            "PA image (p)",
        )
        open_SUM_img = action(
            "Sum image",
            partial(self.action_open_coreg_img, CoImageType.SUM),
            "s",
            "open SUM img",
            "Sum image (s)",
        )
        open_DEBUG_img = action(
            "Debug image",
            partial(self.action_open_coreg_img, CoImageType.DEBUG),
            "v",
            "open debug img",
            "Debug image (v)",
        )

        open_next_image = action(
            get_str("nextImg"),
            self.open_next_image,
            "d",
            "next",
            get_str("nextImgDetail"),
        )

        open_prev_image = action(
            get_str("prevImg"),
            self.open_prev_image,
            "a",
            "prev",
            get_str("prevImgDetail"),
        )

        verify = action(
            get_str("verifyImg"),
            self.verify_image,
            "space",
            "verify",
            get_str("verifyImgDetail"),
        )

        save = action(
            get_str("save"),
            self.save_file,
            "Ctrl+S",
            "save",
            get_str("saveDetail"),
            enabled=False,
        )

        def get_format_meta(format):
            """
            returns a tuple containing (title, icon_name) of the selected format
            """
            if format == LabelFileFormat.ARPAM:
                return "&ARPAM", "format_arpam"
            raise ValueError("Format not supported:", format)

        save_format = action(
            get_format_meta(self.label_file_format)[0],
            self.change_format,
            "Ctrl+",
            get_format_meta(self.label_file_format)[1],
            get_str("changeSaveFormat"),
            enabled=True,
        )

        save_as = action(
            get_str("saveAs"),
            self.save_file_as,
            "Ctrl+Shift+S",
            "save-as",
            get_str("saveAsDetail"),
            enabled=False,
        )

        close = action(
            get_str("closeCur"),
            self.close_file,
            "Ctrl+W",
            "close",
            get_str("closeCurDetail"),
        )

        # delete_image = action(
        # get_str("deleteImg"),
        # self.delete_image,
        # "Ctrl+Shift+D",
        # "close",
        # get_str("deleteImgDetail"),
        # )

        reset_all = action(
            get_str("resetAll"),
            self.reset_all,
            None,
            "resetall",
            get_str("resetAllDetail"),
        )

        color1 = action(
            get_str("boxLineColor"),
            self.choose_color1,
            "Ctrl+L",
            "color_line",
            get_str("boxLineColorDetail"),
        )

        create_mode = action(
            get_str("crtBox"),
            self.set_create_mode,
            "w",
            "new",
            get_str("crtBoxDetail"),
            enabled=False,
        )
        edit_mode = action(
            get_str("editBox"),
            self.set_edit_mode,
            "Ctrl+J",
            "edit",
            get_str("editBoxDetail"),
            enabled=False,
        )

        create = action(
            get_str("crtBox"),
            self.create_shape,
            "w",
            "new",
            get_str("crtBoxDetail"),
            enabled=False,
        )
        delete = action(
            get_str("delBox"),
            self.delete_selected_shape,
            "Delete",
            "delete",
            get_str("delBoxDetail"),
            enabled=False,
        )
        copy = action(
            get_str("dupBox"),
            self.copy_selected_shape,
            "Ctrl+D",
            "copy",
            get_str("dupBoxDetail"),
            enabled=False,
        )

        advanced_mode = action(
            get_str("advancedMode"),
            self.toggle_advanced_mode,
            "Ctrl+Shift+A",
            "expert",
            get_str("advancedModeDetail"),
            checkable=True,
        )

        hide_all = action(
            get_str("hideAllBox"),
            partial(self.toggle_polygons, False),
            "Ctrl+H",
            "hide",
            get_str("hideAllBoxDetail"),
            enabled=False,
        )
        show_all = action(
            get_str("showAllBox"),
            partial(self.toggle_polygons, True),
            "Ctrl+A",
            "hide",
            get_str("showAllBoxDetail"),
            enabled=False,
        )

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoom_widget)
        self.zoom_widget.setWhatsThis(
            "Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas."
            % (format_shortcut("Ctrl+[-+]"), format_shortcut("Ctrl+Wheel"))
        )
        self.zoom_widget.setEnabled(False)

        zoom_in = action(
            get_str("zoomin"),
            partial(self.add_zoom, 10),
            "Ctrl++",
            "zoom-in",
            get_str("zoominDetail"),
            enabled=False,
        )
        zoom_out = action(
            get_str("zoomout"),
            partial(self.add_zoom, -10),
            "Ctrl+-",
            "zoom-out",
            get_str("zoomoutDetail"),
            enabled=False,
        )
        zoom_org = action(
            get_str("originalsize"),
            partial(self.set_zoom, 100),
            "Ctrl+=",
            "zoom",
            get_str("originalsizeDetail"),
            enabled=False,
        )
        fit_window = action(
            get_str("fitWin"),
            self.set_fit_window,
            "Ctrl+F",
            "fit-window",
            get_str("fitWinDetail"),
            checkable=True,
            enabled=False,
        )
        fit_width = action(
            get_str("fitWidth"),
            self.set_fit_width,
            "Ctrl+Shift+F",
            "fit-width",
            get_str("fitWidthDetail"),
            checkable=True,
            enabled=False,
        )
        # Group zoom controls into a list for easier toggling.
        zoom_actions = (
            self.zoom_widget,
            zoom_in,
            zoom_out,
            zoom_org,
            fit_window,
            fit_width,
        )
        self.zoom_mode = self.MANUAL_ZOOM
        self.scalers = {
            self.FIT_WINDOW: self.scale_fit_window,
            self.FIT_WIDTH: self.scale_fit_width,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action(
            get_str("editLabel"),
            self.edit_label,
            "Ctrl+E",
            "edit",
            get_str("editLabelDetail"),
            enabled=False,
        )
        self.edit_button.setDefaultAction(edit)

        shape_line_color = action(
            get_str("shapeLineColor"),
            self.choose_shape_line_color,
            icon="color_line",
            tip=get_str("shapeLineColorDetail"),
            enabled=False,
        )
        shape_fill_color = action(
            get_str("shapeFillColor"),
            self.choose_shape_fill_color,
            icon="color",
            tip=get_str("shapeFillColorDetail"),
            enabled=False,
        )

        labels = self.dock.toggleViewAction()
        labels.setText(get_str("showHide"))
        labels.setShortcut("Ctrl+Shift+L")

        # Label list context menu.
        label_menu = QMenu()
        add_actions(label_menu, (edit, delete))
        self.label_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.label_list.customContextMenuRequested.connect(self.pop_label_list_menu)

        # Draw squares/rectangles
        self.draw_squares_option = QAction(get_str("drawSquares"), self)
        self.draw_squares_option.setShortcut("Ctrl+Shift+R")
        self.draw_squares_option.setCheckable(True)
        self.draw_squares_option.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.draw_squares_option.triggered.connect(self.toggle_draw_square)

        # Store actions for further handling.
        self.actions = Struct(
            save=save,
            save_format=save_format,
            saveAs=save_as,
            open=open,
            close=close,
            resetAll=reset_all,
            # deleteImg=delete_image,
            lineColor=color1,
            create=create,
            delete=delete,
            edit=edit,
            copy=copy,
            createMode=create_mode,
            editMode=edit_mode,
            advancedMode=advanced_mode,
            shapeLineColor=shape_line_color,
            shapeFillColor=shape_fill_color,
            zoom=zoom,
            zoomIn=zoom_in,
            zoomOut=zoom_out,
            zoomOrg=zoom_org,
            fitWindow=fit_window,
            fitWidth=fit_width,
            zoomActions=zoom_actions,
            fileMenuActions=(open, open_dir, save, save_as, close, reset_all, quit),
            beginner=(),
            advanced=(),
            editMenu=(edit, copy, delete, None, color1, self.draw_squares_option),
            beginnerContext=(create, edit, copy, delete),
            advancedContext=(
                create_mode,
                edit_mode,
                edit,
                copy,
                delete,
                shape_line_color,
                shape_fill_color,
            ),
            onLoadActive=(close, create, create_mode, edit_mode),
            onShapesPresent=(save_as, hide_all, show_all),
        )

        self.menus = Struct(
            file=self.menu(get_str("menu_file")),
            edit=self.menu(get_str("menu_edit")),
            view=self.menu(get_str("menu_view")),
            help=self.menu(get_str("menu_help")),
            recentFiles=QMenu(get_str("menu_openRecent")),
            labelList=label_menu,
        )

        # Auto saving : Enable auto saving if pressing next
        self.auto_saving = QAction(get_str("autoSaveMode"), self)
        self.auto_saving.setCheckable(True)
        self.auto_saving.setChecked(settings.get(SETTING_AUTO_SAVE, False))
        # Sync single class mode from PR#106
        self.single_class_mode = QAction(get_str("singleClsMode"), self)
        self.single_class_mode.setShortcut("Ctrl+Shift+S")
        self.single_class_mode.setCheckable(True)
        self.single_class_mode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.display_label_option = QAction(get_str("displayLabel"), self)
        self.display_label_option.setShortcut("Ctrl+Shift+P")
        self.display_label_option.setCheckable(True)
        self.display_label_option.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.display_label_option.triggered.connect(self.toggle_paint_labels_option)

        add_actions(
            self.menus.file,
            (
                open,
                open_dir,
                open_PA_img,
                open_US_img,
                open_SUM_img,
                open_DEBUG_img,
                open_annotation,
                copy_prev_bounding,
                self.menus.recentFiles,
                save,
                save_format,
                save_as,
                close,
                reset_all,
                # delete_image,
                quit,
            ),
        )
        add_actions(
            self.menus.view,
            (
                self.auto_saving,
                self.single_class_mode,
                self.display_label_option,
                labels,
                advanced_mode,
                None,
                hide_all,
                show_all,
                None,
                zoom_in,
                zoom_out,
                zoom_org,
                None,
                fit_window,
                fit_width,
            ),
        )

        self.menus.file.aboutToShow.connect(self.update_file_menu)

        # Custom context menu for the canvas widget:
        add_actions(self.canvas.menus[0], self.actions.beginnerContext)
        add_actions(
            self.canvas.menus[1],
            (
                action("&Copy here", self.copy_shape),
                action("&Move here", self.move_shape),
            ),
        )

        self.tools = self.toolbar("Tools")
        self.actions.beginner = (
            open,
            open_dir,
            open_next_image,
            open_prev_image,
            verify,
            save,
            save_format,
            None,
            create,
            copy,
            delete,
            None,
            zoom_in,
            zoom,
            zoom_out,
            fit_window,
            fit_width,
        )

        self.actions.advanced = (
            open,
            open_dir,
            open_PA_img,
            open_US_img,
            open_SUM_img,
            open_DEBUG_img,
            open_next_image,
            open_prev_image,
            save,
            save_format,
            None,
            create_mode,
            edit_mode,
            None,
            hide_all,
            show_all,
        )

        self.statusBar().showMessage("%s started." % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.file_path = default_filename
        self.last_open_dir = None
        self.recent_files = []
        self.max_recent = 7
        self.line_color = None
        self.fill_color = None
        self.zoom_level = 100
        self.fit_window = False

        # Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recent_file_qstring_list = settings.get(SETTING_RECENT_FILES)
                self.recent_files = [i for i in recent_file_qstring_list]
            else:
                self.recent_files = recent_file_qstring_list = settings.get(
                    SETTING_RECENT_FILES
                )

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        save_dir = settings.get(SETTING_SAVE_DIR, None)
        self.last_open_dir = settings.get(SETTING_LAST_OPEN_DIR, None)
        if (
            self.default_save_dir is None
            and save_dir is not None
            and os.path.exists(save_dir)
        ):
            self.default_save_dir = save_dir
            self.statusBar().showMessage(
                "%s started. Annotation will be saved to %s"
                % (__appname__, self.default_save_dir)
            )
            self.statusBar().show()

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.line_color = QColor(
            settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR)
        )
        Shape.fill_color = self.fill_color = QColor(
            settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR)
        )
        self.canvas.set_drawing_color(self.line_color)

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggle_advanced_mode()

        # Populate the File menu dynamically.
        self.update_file_menu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.file_path and os.path.isdir(self.file_path):
            self.queue_event(partial(self.import_dir_images, self.file_path or ""))
        elif self.file_path:
            self.queue_event(partial(self.load_file, self.file_path or ""))

        # Callbacks:
        self.zoom_widget.valueChanged.connect(self.paint_canvas)

        self.populate_mode_actions()

        # Display cursor coordinates at the right of status bar
        self.label_coordinates = QLabel("")
        self.statusBar().addPermanentWidget(self.label_coordinates)

        # Open Dir if default file
        if self.file_path and os.path.isdir(self.file_path):
            self.open_dir_dialog(dir_path=self.file_path, silent=True)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.canvas.set_drawing_shape_to_square(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.set_drawing_shape_to_square(True)

    # Support Functions #
    def set_format(self, save_format):
        ...

    def change_format(self):
        ...

    def no_shapes(self):
        return not self.items_to_shapes

    def toggle_advanced_mode(self, value=True):
        self._beginner = not value
        self.canvas.set_editing(True)
        self.populate_mode_actions()
        self.edit_button.setVisible(not value)
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dock_features)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dock_features)

    def populate_mode_actions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        add_actions(self.tools, tool)
        self.canvas.menus[0].clear()
        add_actions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (
            (self.actions.create,)
            if self.beginner()
            else (self.actions.createMode, self.actions.editMode)
        )
        add_actions(self.menus.edit, actions + self.actions.editMenu)

    def set_beginner(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.beginner)

    def set_advanced(self):
        self.tools.clear()
        add_actions(self.tools, self.actions.advanced)

    def set_dirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def set_clean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggle_actions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queue_event(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def reset_state(self):
        self.items_to_shapes.clear()
        self.shapes_to_items.clear()
        self.label_list.clear()
        self.file_path = None
        self.image_data = None
        self.label_file = None
        self.canvas.reset_state()
        self.label_coordinates.clear()
        self.combo_box.cb.clear()

    def current_item(self):
        items = self.label_list.selectedItems()
        if items:
            return items[0]
        return None

    def add_recent_file(self, file_path):
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        elif len(self.recent_files) >= self.max_recent:
            self.recent_files.pop()
        self.recent_files.insert(0, file_path)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def show_info_dialog(self):
        from libs.__init__ import __version__

        msg = "Name:{0} \nApp Version:{1} \n{2} ".format(
            __appname__, __version__, sys.version_info
        )
        QMessageBox.information(self, "Information", msg)

    def create_shape(self):
        assert self.beginner()
        self.canvas.set_editing(False)
        self.actions.create.setEnabled(False)

    def toggle_drawing_sensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print("Cancel creation.")
            self.canvas.set_editing(True)
            self.canvas.restore_cursor()
            self.actions.create.setEnabled(True)

    def toggle_draw_mode(self, edit=True):
        self.canvas.set_editing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def set_create_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(False)

    def set_edit_mode(self):
        assert self.advanced()
        self.toggle_draw_mode(True)
        self.label_selection_changed()

    def update_file_menu(self):
        curr_file_path = self.file_path

        def exists(filename):
            return os.path.exists(filename)

        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recent_files if f != curr_file_path and exists(f)]
        for i, f in enumerate(files):
            icon = new_icon("labels")
            action = QAction(icon, "&%d %s" % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.load_recent, f))
            menu.addAction(action)

    def pop_label_list_menu(self, point):
        self.menus.labelList.exec_(self.label_list.mapToGlobal(point))

    def edit_label(self):
        if not self.canvas.editing():
            return
        item = self.current_item()
        if not item:
            return
        text = self.label_dialog.pop_up(item.text())
        if text is not None:
            item.setText(text)
            item.setBackground(generate_color_by_text(text))
            self.set_dirty()
            self.update_combo_box()

    # Tzutalin 20160906 : Add file list and dock to move faster
    def file_item_double_clicked(self, item=None):
        self.cur_img_idx = self.m_img_list.index(item.text())
        filename = self.m_img_list[self.cur_img_idx]
        if filename:
            self.load_file(filename)

    # React to canvas signals.
    def shape_selection_changed(self, selected=False):
        if self._no_selection_slot:
            self._no_selection_slot = False
        else:
            shape = self.canvas.selected_shape
            if shape:
                self.shapes_to_items[shape].setSelected(True)
            else:
                self.label_list.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)

    def add_label(self, shape):
        shape.paint_label = self.display_label_option.isChecked()
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setBackground(generate_color_by_text(shape.label))
        self.items_to_shapes[item] = shape
        self.shapes_to_items[shape] = item
        self.label_list.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)
        self.update_combo_box()

    def remove_label(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapes_to_items[shape]
        self.label_list.takeItem(self.label_list.row(item))
        del self.shapes_to_items[shape]
        del self.items_to_shapes[item]
        self.update_combo_box()

    def load_labels(self, shapes):
        s = []
        for label, points, line_color, fill_color in shapes:
            shape = Shape(label=label)
            for x, y in points:
                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snap_point_to_canvas(x, y)
                if snapped:
                    self.set_dirty()

                shape.add_point(QPointF(x, y))
            shape.close()
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                shape.line_color = generate_color_by_text(label)

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                shape.fill_color = generate_color_by_text(label)

            self.add_label(shape)
        self.update_combo_box()
        self._s = s
        self.canvas.load_shapes(s)

    def update_combo_box(self):
        # Get the unique labels and add them to the Combobox.
        items_text_list = [
            str(self.label_list.item(i).text()) for i in range(self.label_list.count())
        ]

        unique_text_list = list(set(items_text_list))
        # Add a null row for showing all the labels
        unique_text_list.append("")
        unique_text_list.sort()

        self.combo_box.update_items(unique_text_list)

    def save_labels(self, annotation_file_path: str):
        if self.label_file is None:
            self.label_file = LabelFile()
            self.label_file.verified = self.canvas.verified

        def format_shape(s):
            return dict(
                label=s.label,
                line_color=s.line_color.getRgb(),
                fill_color=s.fill_color.getRgb(),
                points=[(p.x(), p.y()) for p in s.points],
            )

        shapes = [format_shape(shape) for shape in self.canvas.shapes]
        # Can add different annotation formats here
        try:
            assert self.label_file_format == LabelFileFormat.ARPAM
            self.label_file.save_arpam_format(shapes, self.file_path, self.image_data)
            print(
                "Image:{0} -> Annotation:{1}".format(
                    self.file_path, annotation_file_path
                )
            )
            return True
        except LabelFileError as e:
            self.error_message("Error saving label data", "<b>%s</b>" % e)
            return False

    def copy_selected_shape(self):
        self.add_label(self.canvas.copy_selected_shape())
        # fix copy and delete
        self.shape_selection_changed(True)

    def combo_selection_changed(self, index):
        text = self.combo_box.cb.itemText(index)
        for i in range(self.label_list.count()):
            if text == "":
                self.label_list.item(i).setCheckState(2)
            elif text != self.label_list.item(i).text():
                self.label_list.item(i).setCheckState(0)
            else:
                self.label_list.item(i).setCheckState(2)

    def label_selection_changed(self):
        item = self.current_item()
        if item and self.canvas.editing():
            self._no_selection_slot = True
            self.canvas.select_shape(self.items_to_shapes[item])
            shape = self.items_to_shapes[item]

    def label_item_changed(self, item):
        shape = self.items_to_shapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            shape.line_color = generate_color_by_text(shape.label)
            self.set_dirty()
        else:  # User probably changed item visibility
            self.canvas.set_shape_visible(shape, item.checkState() == Qt.Checked)

    # Callback functions:
    def new_shape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if (
            not self.use_default_label_checkbox.isChecked()
            or not self.default_label_text_line.text()
        ):
            if len(self.label_hist) > 0:
                self.label_dialog = LabelDialog(parent=self, list_item=self.label_hist)

            # Sync single class mode from PR#106
            if self.single_class_mode.isChecked() and self.lastLabel:
                text = self.lastLabel
            else:
                text = self.label_dialog.pop_up(text=self.prev_label_text)
                self.lastLabel = text
        else:
            text = self.default_label_text_line.text()

        if text is not None:
            self.prev_label_text = text
            generate_color = generate_color_by_text(text)
            shape = self.canvas.set_last_label(text, generate_color, generate_color)
            self.add_label(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.set_editing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.set_dirty()

            if text not in self.label_hist:
                self.label_hist.append(text)
        else:
            # self.canvas.undoLastLine()
            self.canvas.reset_all_lines()

    def scroll_request(self, delta, orientation):
        units = -delta / (8 * 15)
        bar = self.scroll_bars[orientation]
        bar.setValue(int(bar.value() + bar.singleStep() * units))

    def set_zoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.MANUAL_ZOOM
        self.zoom_widget.setValue(value)

    def add_zoom(self, increment=10):
        self.set_zoom(self.zoom_widget.value() + increment)

    def zoom_request(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scroll_bars[Qt.Horizontal]
        v_bar = self.scroll_bars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scroll_area.width()
        h = self.scroll_area.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.add_zoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(int(new_h_bar_value))
        v_bar.setValue(int(new_v_bar_value))

    def set_fit_window(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoom_mode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def set_fit_width(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoom_mode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjust_scale()

    def toggle_polygons(self, value):
        for item, shape in self.items_to_shapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def load_file(self, file_path: Optional[str] = None):
        """Load the specified file, or the last opened file if None."""
        self.reset_state()
        self.canvas.setEnabled(False)
        fpath = Path(file_path)
        if fpath.is_dir():
            self.statusBar().showMessage(f"Error: {file_path} is a directory.")
            return

        if file_path is None:
            file_path = self.settings.get(SETTING_FILENAME)

        file_path = os.path.abspath(file_path)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if file_path and self.file_list_widget.count() > 0:
            if file_path in self.m_img_list:
                index = self.m_img_list.index(file_path)
                file_widget_item = self.file_list_widget.item(index)
                file_widget_item.setSelected(True)
            else:
                self.file_list_widget.clear()
                self.m_img_list.clear()
                self.m_img_list_all.clear()
                self.m_img_list_filtered.clear()

        if file_path and os.path.exists(file_path):
            if LabelFile.is_label_file(file_path):
                try:
                    self.label_file = LabelFile(file_path)
                except LabelFileError as e:
                    self.error_message(
                        "Error opening file",
                        (
                            "<p><b>%s</b></p>"
                            "<p>Make sure <i>%s</i> is a valid label file."
                        )
                        % (e, file_path),
                    )
                    self.status("Error reading %s" % file_path)
                    return False
                self.image_data = self.label_file.image_data
                self.line_color = QColor(*self.label_file.lineColor)
                self.fill_color = QColor(*self.label_file.fillColor)
                self.canvas.verified = self.label_file.verified
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.image_data = read(file_path, None)
                self.label_file = None
                if self.label_file_format == LabelFileFormat.ARPAM:
                    ### Main read new roi file here
                    try:
                        self.label_file = LabelFile(filename=file_path, arpam=True)
                    except Exception as e:
                        print(e)
                        self.status(str(e))
                        return

                    img_set = self.label_file.arpam_img_set
                    self.arpam_img_type = img_set.init_type

                    # update img meta display
                    img_meta = self.label_file.arpam_img_meta
                    if img_meta:
                        self.img_meta_label.setText(
                            "".join(
                                (
                                    f"{self.label_file.arpam_roi_file.fid}\n",
                                    f"PA dB: {img_meta.dB:.3f}\n",
                                    f"mean ratio: {img_meta.mean_ratio:.3f}\n",
                                    f"balloon mean: {img_meta.bal_mean:.3f}\n",
                                    f"balloon std: {img_meta.bal_std:.3f}\n",
                                    f"tissue mean: {img_meta.under_mean:.3f}\n",
                                    f"tissue std: {img_meta.under_std:.3f}\n",
                                )
                            )
                        )
                    else:
                        self.img_meta_label.setText(
                            f"Error: Metadata file not found:\n{self.label_file.arpam_roi_file.img_set.meta}"
                        )

                self.canvas.verified = False

            if isinstance(self.image_data, QImage):
                image = self.image_data
            else:
                image = QImage.fromData(self.image_data)
            if image.isNull():
                self.error_message(
                    "Error opening file",
                    "<p>Make sure <i>%s</i> is a valid image file." % file_path,
                )
                self.status("Error reading %s" % file_path)
                return False
            self.status(f"Loaded {os.path.basename(file_path)} ({self.arpam_img_type})")
            self.image = image
            self.file_path = file_path
            self.canvas.load_pixmap(QPixmap.fromImage(image))
            # if self.label_file:
            # self.load_labels(self.label_file.shapes)
            self.set_clean()
            self.canvas.setEnabled(True)
            self.adjust_scale(initial=True)
            self.paint_canvas()
            self.add_recent_file(self.file_path)
            self.toggle_actions(True)
            self.show_bounding_box_from_annotation_file(file_path)

            counter = self.counter_str()
            self.setWindowTitle(__appname__ + " " + file_path + " " + counter)

            # Default : select last item if there is at least one item
            if self.label_list.count():
                self.label_list.setCurrentItem(
                    self.label_list.item(self.label_list.count() - 1)
                )
                self.label_list.item(self.label_list.count() - 1).setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False

    def load_coregistered_file(self, fpath: str):
        # Highlight the file item
        if fpath and self.file_list_widget.count() > 0:
            if fpath in self.m_img_list:
                index = self.m_img_list.index(fpath)
                file_widget_item = self.file_list_widget.item(index)
                file_widget_item.setSelected(True)
            else:
                self.file_list_widget.clear()
                self.m_img_list_all.clear()
                self.m_img_list_filtered.clear()

        # Load image:
        # read data first and store for saving into label file.
        self.image_data = read(fpath, None)

        if isinstance(self.image_data, QImage):
            image = self.image_data
        else:
            image = QImage.fromData(self.image_data)

        if image.isNull():
            self.error_message(
                "Error opening file",
                "<p>Make sure <i>%s</i> is a valid image file." % fpath,
            )
            self.status("Error reading %s" % fpath)
            return False

        self.status(f"Loaded {os.path.basename(fpath)} ({self.arpam_img_type})")
        self.image = image
        self.file_path = fpath
        self.canvas.load_pixmap(QPixmap.fromImage(image))

        # self.canvas.setEnabled(True)
        # self.adjust_scale(initial=True)
        # self.paint_canvas()
        self.add_recent_file(self.file_path)
        # self.toggle_actions(True)
        self.canvas.load_shapes(self._s)

        counter = self.counter_str()
        self.setWindowTitle(__appname__ + " " + fpath + " " + counter)

        # # Default : select last item if there is at least one item
        # if self.label_list.count():
        # self.label_list.setCurrentItem(self.label_list.item(self.label_list.count() - 1))
        # self.label_list.item(self.label_list.count() - 1).setSelected(True)

        # self.canvas.setFocus(True)
        return True

    def counter_str(self):
        """
        Converts image counter to string representation.
        """
        return "[{} / {}]".format(self.cur_img_idx + 1, len(self.m_img_list))

    def show_bounding_box_from_annotation_file(self, file_path):
        if self.label_file_format == LabelFileFormat.ARPAM:
            self.load_arpam_by_img_path(file_path)

    def resizeEvent(self, event):
        if (
            self.canvas
            and not self.image.isNull()
            and self.zoom_mode != self.MANUAL_ZOOM
        ):
            self.adjust_scale()
        super(MainWindow, self).resizeEvent(event)

    def paint_canvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoom_widget.value()
        self.canvas.label_font_size = int(
            0.02 * max(self.image.width(), self.image.height())
        )
        self.canvas.adjustSize()
        self.canvas.update()

    def adjust_scale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoom_mode]()
        self.zoom_widget.setValue(int(100 * value))

    def scale_fit_window(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scale_fit_width(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.may_continue():
            event.ignore()
        settings = self.settings
        # If it loads images from dir, don't load it at the beginning
        if self.dir_name is None:
            settings[SETTING_FILENAME] = self.file_path if self.file_path else ""
        else:
            settings[SETTING_FILENAME] = ""

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.line_color
        settings[SETTING_FILL_COLOR] = self.fill_color
        settings[SETTING_RECENT_FILES] = self.recent_files
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.default_save_dir and os.path.exists(self.default_save_dir):
            settings[SETTING_SAVE_DIR] = self.default_save_dir
        else:
            settings[SETTING_SAVE_DIR] = ""

        if self.last_open_dir and os.path.exists(self.last_open_dir):
            settings[SETTING_LAST_OPEN_DIR] = self.last_open_dir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ""

        settings[SETTING_AUTO_SAVE] = self.auto_saving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.single_class_mode.isChecked()
        settings[SETTING_PAINT_LABEL] = self.display_label_option.isChecked()
        settings[SETTING_DRAW_SQUARE] = self.draw_squares_option.isChecked()
        settings[SETTING_LABEL_FILE_FORMAT] = self.label_file_format
        settings.save()

    def load_recent(self, filename):
        if self.may_continue():
            self.load_file(filename)

    def scan_all_images(self, folder_path: str) -> List[str]:
        extensions = tuple(
            ".%s" % fmt.data().decode("ascii").lower()
            for fmt in QImageReader.supportedImageFormats()
        )
        images = []

        # Only grab images in the data root
        root, dirs, files = next(os.walk(folder_path))
        # for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(extensions):
                relative_path = os.path.join(root, file)
                path = os.path.abspath(relative_path)
                images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images

    def change_save_dir_dialog(self, _value=False):
        if self.default_save_dir is not None:
            path = self.default_save_dir
        else:
            path = "."

        dir_path = QFileDialog.getExistingDirectory(
            self,
            "%s - Save annotations to the directory" % __appname__,
            path,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )

        if dir_path is not None and len(dir_path) > 1:
            self.default_save_dir = dir_path

        self.statusBar().showMessage(
            "%s . Annotation will be saved to %s"
            % ("Change saved folder", self.default_save_dir)
        )
        self.statusBar().show()

    def open_annotation_dialog(self, _value=False):
        if self.file_path is None:
            self.statusBar().showMessage("Please select image first")
            self.statusBar().show()
            return

        path = os.path.dirname(self.file_path) if self.file_path else "."

    def open_dir_dialog(self, _value=False, dir_path=None, silent=False):
        if not self.may_continue():
            return

        default_open_dir_path = dir_path if dir_path else "."
        if self.last_open_dir and os.path.exists(self.last_open_dir):
            default_open_dir_path = self.last_open_dir
        else:
            default_open_dir_path = (
                os.path.dirname(self.file_path) if self.file_path else "."
            )
        if silent != True:
            target_dir_path = QFileDialog.getExistingDirectory(
                self,
                "%s - Open Directory" % __appname__,
                default_open_dir_path,
                QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
            )

        else:
            target_dir_path = default_open_dir_path
        self.last_open_dir = target_dir_path
        self.import_dir_images(target_dir_path)

    def _update_filtered_img_list(self):
        if self._last_filter_checked:
            filtered = []
            for img_path in self.m_img_list_all:
                try:
                    img_set = arpam_roi.CoImageSet.from_path(img_path)
                except ValueError as e:
                    print(e)
                    continue
                img_meta_p = img_set.meta
                if img_meta_p.exists():
                    img_meta = arpam_meta.ImgMeta.from_path(img_meta_p)

                    if img_meta.mean_ratio > self._filter_thresh:
                        filtered.append(img_path)

            self.m_img_list_filtered = filtered
            self.m_img_list = filtered

        else:
            self.m_img_list = self.m_img_list_all

        self._update_QList_files()

    def _update_QList_files(self):
        self.file_list_widget.clear()
        for imgPath in self.m_img_list:
            item = QListWidgetItem(imgPath)
            self.file_list_widget.addItem(item)

    def import_dir_images(self, dir_path):
        if not self.may_continue() or not dir_path:
            return

        self.last_open_dir = dir_path
        self.dir_name = dir_path
        self.file_path = None
        self.file_list_widget.clear()
        self.m_img_list_all = self.scan_all_images(dir_path)

        # Generate Filter list
        self._update_filtered_img_list()

        self.open_next_image()
        self._update_QList_files()

    def verify_image(self, _value=False):
        # Proceeding next image without dialog if having any label
        if self.file_path is not None:
            try:
                self.label_file.toggle_verify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.save_file()
                if self.label_file is not None:
                    self.label_file.toggle_verify()
                else:
                    return

            self.canvas.verified = self.label_file.verified
            self.paint_canvas()
            self.save_file()

    def open_prev_image(self, _value=False):
        # Proceeding prev image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return

        if not self.may_continue():
            return

        if len(self.m_img_list) <= 0:
            return

        if self.file_path is None:
            return

        self.cur_img_idx = (self.cur_img_idx - 1) % len(self.m_img_list)
        filename = self.m_img_list[self.cur_img_idx]
        if filename:
            try:
                self.load_file(filename)
            except Exception as e:
                print(e)
                self.status(str(e))

    def action_open_coreg_img(self, coreg_type: CoImageType, _value=False):
        if (
            self.arpam_img_type != coreg_type
            and self.label_file
            and self.label_file.arpam_roi_file
        ):
            try:
                img_path = str(
                    self.label_file.arpam_roi_file.img_set.to_type(coreg_type)
                )
            except ValueError as e:
                print(e)
                img_path = str(self.label_file.arpam_roi_file.img_set.Debug)

            try:
                # update index
                self.cur_img_idx = self.m_img_list.index(img_path)
            except ValueError as e:  # file not found
                print(e)
                return

            self.arpam_img_type = coreg_type
            self.load_coregistered_file(img_path)

    def open_next_image(self, _value=False):
        # Proceeding prev image without dialog if having any label
        if self.auto_saving.isChecked():
            if self.default_save_dir is not None:
                if self.dirty is True:
                    self.save_file()
            else:
                self.change_save_dir_dialog()
                return

        if not self.may_continue():
            return

        if len(self.m_img_list) <= 0:
            return

        filename = None

        if self.file_path is None:
            filename = self.m_img_list[0]
            self.cur_img_idx = 0
        else:
            self.cur_img_idx = (self.cur_img_idx + 1) % len(self.m_img_list)
            filename = self.m_img_list[self.cur_img_idx]

        if filename:
            self.load_file(filename)

    def open_file(self, _value=False):
        if not self.may_continue():
            return
        path = os.path.dirname(self.file_path) if self.file_path else "."
        formats = [
            "*.%s" % fmt.data().decode("ascii").lower()
            for fmt in QImageReader.supportedImageFormats()
        ]
        filters = "Image & Label files (%s)" % " ".join(
            formats + ["*%s" % LabelFile.suffix]
        )
        filename = QFileDialog.getOpenFileName(
            self, "%s - Choose Image or Label file" % __appname__, path, filters
        )
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.cur_img_idx = 0
            self.load_file(filename)

    def save_file(self, _value=False):
        if self.default_save_dir is not None and len(self.default_save_dir):
            if self.file_path:
                image_file_name = os.path.basename(self.file_path)
                saved_file_name = os.path.splitext(image_file_name)[0]
                saved_path = os.path.join(self.default_save_dir, saved_file_name)
                self._save_file(saved_path)
        else:
            image_file_dir = os.path.dirname(self.file_path)
            image_file_name = os.path.basename(self.file_path)
            saved_file_name = os.path.splitext(image_file_name)[0]
            saved_path = os.path.join(image_file_dir, saved_file_name)
            self._save_file(
                saved_path
                if self.label_file
                else self.save_file_dialog(remove_ext=False)
            )

    def save_file_as(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._save_file(self.save_file_dialog())

    def save_file_dialog(self, remove_ext=True):
        caption = "%s - Choose File" % __appname__
        filters = "File (*%s)" % LabelFile.suffix
        open_dialog_path = self.current_path()
        dlg = QFileDialog(self, caption, open_dialog_path, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filename_without_extension = os.path.splitext(self.file_path)[0]
        dlg.selectFile(filename_without_extension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            full_file_path = dlg.selectedFiles()[0]
            if remove_ext:
                return os.path.splitext(full_file_path)[
                    0
                ]  # Return file path without the extension.
            else:
                return full_file_path
        return ""

    def _save_file(self, annotation_file_path):
        if annotation_file_path and self.save_labels(annotation_file_path):
            self.set_clean()
            self.statusBar().showMessage("Saved to  %s" % annotation_file_path)
            self.statusBar().show()

    def close_file(self, _value=False):
        if not self.may_continue():
            return
        self.reset_state()
        self.set_clean()
        self.toggle_actions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    # def delete_image(self):
    # delete_path = self.file_path
    # if delete_path is not None:
    # self.open_next_image()
    # self.cur_img_idx -= 1
    # self.img_count -= 1
    # if os.path.exists(delete_path):
    # os.remove(delete_path)
    # self.import_dir_images(self.last_open_dir)

    def reset_all(self):
        self.settings.reset()
        self.close()
        process = QProcess()
        process.startDetached(os.path.abspath(__file__))

    def may_continue(self):
        if not self.dirty:
            return True
        else:
            discard_changes = self.discard_changes_dialog()
            if discard_changes == QMessageBox.No:
                return True
            elif discard_changes == QMessageBox.Yes:
                self.save_file()
                return True
            else:
                return False

    def discard_changes_dialog(self):
        yes, no, cancel = QMessageBox.Yes, QMessageBox.No, QMessageBox.Cancel
        msg = 'You have unsaved changes, would you like to save them and proceed?\nClick "No" to undo all changes.'
        return QMessageBox.warning(self, "Attention", msg, yes | no | cancel)

    def error_message(self, title, message):
        return QMessageBox.critical(
            self, title, "<p><b>%s</b></p>%s" % (title, message)
        )

    def current_path(self):
        return os.path.dirname(self.file_path) if self.file_path else "."

    def choose_color1(self):
        color = self.color_dialog.getColor(
            self.line_color, "Choose line color", default=DEFAULT_LINE_COLOR
        )
        if color:
            self.line_color = color
            Shape.line_color = color
            self.canvas.set_drawing_color(color)
            self.canvas.update()
            self.set_dirty()

    def delete_selected_shape(self):
        self.remove_label(self.canvas.delete_selected())
        self.set_dirty()
        if self.no_shapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    def choose_shape_line_color(self):
        color = self.color_dialog.getColor(
            self.line_color, "Choose Line Color", default=DEFAULT_LINE_COLOR
        )
        if color:
            self.canvas.selected_shape.line_color = color
            self.canvas.update()
            self.set_dirty()

    def choose_shape_fill_color(self):
        color = self.color_dialog.getColor(
            self.fill_color, "Choose Fill Color", default=DEFAULT_FILL_COLOR
        )
        if color:
            self.canvas.selected_shape.fill_color = color
            self.canvas.update()
            self.set_dirty()

    def copy_shape(self):
        self.canvas.end_move(copy=True)
        self.add_label(self.canvas.selected_shape)
        self.set_dirty()

    def move_shape(self):
        self.canvas.end_move(copy=False)
        self.set_dirty()

    def load_predefined_classes(self, predef_classes_file):
        if os.path.exists(predef_classes_file) is True:
            with codecs.open(predef_classes_file, "r", "utf8") as f:
                for line in f:
                    line = line.strip()
                    if self.label_hist is None:
                        self.label_hist = [line]
                    else:
                        self.label_hist.append(line)

    def load_arpam_by_img_path(self, img_path):
        # TODO
        self.arpam_roi_file = arpam_roi.ROI_File.from_img_path(img_path)
        shapes = []

        for bbox in self.arpam_roi_file.bboxes:
            x_max = round(bbox.xmax * self.arpam_roi_file.size.w)
            x_min = round(bbox.xmin * self.arpam_roi_file.size.w)
            y_max = round(bbox.ymax * self.arpam_roi_file.size.h)
            y_min = round(bbox.ymin * self.arpam_roi_file.size.h)

            points = [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]

            shape = (bbox.name, points, None, None)
            shapes.append(shape)

        self.load_labels(shapes)
        self.canvas.verified = True

    def copy_previous_bounding_boxes(self):
        current_index = self.m_img_list.index(self.file_path)
        if current_index - 1 >= 0:
            prev_file_path = self.m_img_list[current_index - 1]
            self.show_bounding_box_from_annotation_file(prev_file_path)
            self.save_file()

    def toggle_paint_labels_option(self):
        for shape in self.canvas.shapes:
            shape.paint_label = self.display_label_option.isChecked()

    def toggle_draw_square(self):
        self.canvas.set_drawing_shape_to_square(self.draw_squares_option.isChecked())


def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        reader = QImageReader(filename)
        reader.setAutoTransform(True)
        return reader.read()
    except:
        return default


def get_main_app(argv=None):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    if not argv:
        argv = []
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(new_icon("app"))
    # Tzutalin 201705+: Accept extra agruments to change predefined class file
    argparser = argparse.ArgumentParser()
    argparser.add_argument("image_dir", nargs="?")
    argparser.add_argument(
        "class_file",
        default=os.path.join(
            os.path.dirname(__file__), "data", "predefined_classes.txt"
        ),
        nargs="?",
    )
    argparser.add_argument("save_dir", nargs="?")
    args = argparser.parse_args(argv[1:])

    args.image_dir = args.image_dir and os.path.normpath(args.image_dir)
    args.class_file = args.class_file and os.path.normpath(args.class_file)
    args.save_dir = args.save_dir and os.path.normpath(args.save_dir)

    # Usage : labelImg.py image classFile saveDir
    win = MainWindow(args.image_dir, args.class_file, args.save_dir)
    win.show()
    return app, win


def main():
    """construct main app and run it"""
    app, _win = get_main_app(sys.argv)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
