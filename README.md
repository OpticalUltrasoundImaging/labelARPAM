# LabelARPAM

LabelARPAM (a fork of [LabelImg](https://github.com/tzutalin/labelImg)) is a graphical image annotation tool specifically to deal with coregistered US/ARPAM images.
It extensively uses [python-arpamutils](https://github.com/OpticalUltrasoundImaging/python-arpamutils) internally.

## Installation

1. Install [python-arpamutils](https://github.com/OpticalUltrasoundImaging/python-arpamutils) in a new conda environment. 
2. Clone this repo

```
git clone git@github.com:OpticalUltrasoundImaging/labelARPAM.git
```

3. Install PyQt5

```
python -m pip install PyQt5
```

4. Generate Qt resources

```
pyrcc5 -o libs/resources.py resources.qrc
```

5. Start LabelARPAM

```
python labelImg.py
```

## Usage

1. Follow the instructions above to install and start the application.
2. Click "Open Dir" (Ctrl+u) to open a patient directory that contains `meta` and `roi` directories.

**View images in a coregistered image set**
* click "Show PA" or type `p` to view the PA image.
* click "Show US" or type `u` to view the US image.
* click "Show Sum" or type `s` to view the Sum image.
* Click "Show Debug" or type `v` to view the Debug image.

**Navigate between images**
* Click "Next Image" or type `d` to move to the next image set
* Click "Prev Image" or type`a` to move to the previous image set

**Edit bounding boxes**
* Existing label boxes should display automatically
* Click "Create RectBox" or type `w` to enter create mode. Drag to create new boxes.
* Click "Edit RectBox" or type `e` to enter edit mode. Resize boxes or change box labels.
* `ctrl-d` to duplicate the selected box.
* `ctrl-v` to copy the bboxes from the previous image to the current image.

In the top right corner, check "Good PA data" and/or "Good US data" to mark these images as good/usable.
