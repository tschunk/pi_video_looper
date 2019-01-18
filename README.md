# pi_video_looper
Application to turn your Raspberry Pi into a dedicated looping video playback device, good for art installations, information displays, or just playing cat videos all day.

# Video Kiosk
There is now also a kiosk mode where users can select video via buttons connected to Raspi's GPIO ports or a touchscreen.
To use this mode, just run `install_kiosk.sh` instead of normal `install.sh` when following the instructions from https://learn.adafruit.com/raspberry-pi-video-looper/installation.

Kiosk mode is configured via a file kiosk.ini which must be stored next to the video files.
The order of videos in the kiosk menu is the same as in the `kiosk.ini` file.

Example `kiosk.ini` config file:
```
[movie.mp4]
title=Title
thumbnail=thumbnail.png
gpio=5
```

Explanation of options:
- `[movie.mp4]`: section name must be filename of the video file
- `title`: text that is shown below the thumbnail
- `thumbnail`: filename of an image that is shown as a thumbnail for the video. If not set, a number is shown.
- `gpio`: GPIO pin number (BCM numbering scheme) which is associated with this movie. Rising edge on this pin will start the movie.
