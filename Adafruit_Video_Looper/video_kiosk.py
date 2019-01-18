# -*- coding: utf-8 -*-
import ConfigParser
import math
import os
import signal
import sys
import time

import pygame
try:
    import RPi.GPIO as GPIO
except (ImportError, RuntimeError):
    print('GPIO not supported or not available!')
    GPIO = None

from model import Playlist
from video_looper import VideoLooper


def grid_enumerate(items, items_per_row, start=0):
    col = row = 0
    for i, item in enumerate(items, start=start):
        yield i, col, row, item
        col += 1
        if col == items_per_row:
            col = 0
            row += 1


def aspect_scale(img, (bx, by), smooth=True):
    """ Scales 'img' to fit into box bx/by.
     This method will retain the original image's aspect ratio """
    ix, iy = img.get_size()
    if ix > iy:
        # fit to width
        scale_factor = bx / float(ix)
        sy = scale_factor * iy
        if sy > by:
            scale_factor = by / float(iy)
            sx = scale_factor * ix
            sy = by
        else:
            sx = bx
    else:
        # fit to height
        scale_factor = by / float(iy)
        sx = scale_factor * ix
        if sx > bx:
            scale_factor = bx / float(ix)
            sx = bx
            sy = scale_factor * iy
        else:
            sy = by
    if smooth:
        return pygame.transform.smoothscale(img, (int(sx), int(sy)))
    else:
        return pygame.transform.scale(img, (int(sx), int(sy)))


class Movie(object):
    def __init__(self, fpath, title, on_gpio=None, thumbnail_fpath=None):
        self.path = fpath
        self.title = title
        self.on_gpio = on_gpio
        self.thumbnail_fpath = thumbnail_fpath
        if thumbnail_fpath:
            self.thumbnail = pygame.image.load(thumbnail_fpath)
        else:
            self.thumbnail = None

    def __repr__(self):
        return '<Movie: {0.title} [{0.path} / {0.thumbnail_fpath}] @ GPIO {0.on_gpio}'.format(self)


class VideoKiosk(VideoLooper):
    def __init__(self, *args, **kwargs):
        super(VideoKiosk, self).__init__(*args, **kwargs)
        self.__last_gpio_press = None
        self.__video_selection_regions = None
        self.__menu_screen = None

        try:
            window_size = self._config.get('video_looper', 'window_size').strip()
            size = map(int, window_size.split('x'))
            os.environ['SDL_VIDEO_CENTERED'] = '1'
            self._screen = pygame.display.set_mode(size, 0)
        except ConfigParser.NoOptionError:
            pass

        if self._keyboard_control:
            pygame.mouse.set_visible(True)

    def _load_kiosk_config(self, ini_path, movie_root):
        kiosk_cfg = ConfigParser.SafeConfigParser()
        kiosk_cfg.read(ini_path)
        movies = list()

        for movie_fname in kiosk_cfg.sections():
            movie_fpath = os.path.join(movie_root, movie_fname)
            if not os.path.exists(movie_fpath):
                self._print('File not found: {}'.format(movie_fpath))
                continue

            title = kiosk_cfg.get(movie_fname, 'title').strip()
            try:
                gpio_pin = kiosk_cfg.getint(movie_fname, 'gpio')
            except ConfigParser.NoOptionError:
                gpio_pin = None

            try:
                thumbnail_fname = kiosk_cfg.get(movie_fname, 'thumbnail').strip()
                thumbnail_fpath = os.path.join(movie_root, thumbnail_fname)
                if not os.path.exists(thumbnail_fpath):
                    self._print('Thumbnail not found: {}'.format(thumbnail_fpath))
                    thumbnail_fpath = None
            except ConfigParser.NoOptionError:
                thumbnail_fpath = None

            new_movie = Movie(
                title=title,
                fpath=movie_fpath,
                on_gpio=gpio_pin,
                thumbnail_fpath=thumbnail_fpath
            )
            self._print('Found movie: ' + repr(new_movie))

            movies.append(new_movie)
        return movies

    def _build_playlist(self):
        # Get list of paths to search from the file reader.
        paths = self._reader.search_paths()
        # Enumerate all movie files inside those paths.
        movies = []

        for path in paths:
            # Skip paths that don't exist or are files.
            if not os.path.exists(path) or not os.path.isdir(path):
                continue
            kiosk_config_ini = os.path.join(path, 'kiosk.ini')
            if not os.path.exists(kiosk_config_ini):
                self._print('No kiosk.ini in {}. Skipping...'.format(path))
                continue
            found_movies = self._load_kiosk_config(kiosk_config_ini, path)
            movies.extend(found_movies)

            # Get the video volume from the file in the usb key
            sound_vol_file_path = '{0}/{1}'.format(path.rstrip('/'), self._sound_vol_file)
            if os.path.exists(sound_vol_file_path):
                with open(sound_vol_file_path, 'r') as sound_file:
                    sound_vol_string = sound_file.readline()
                    if self._is_number(sound_vol_string):
                        self._sound_vol = int(float(sound_vol_string))
        new_playlist = Playlist(movies, self._is_random)
        return new_playlist

    def __render_playlist_items(self, playlist, surface_size):
        # each item consists of an optional thumbnail and a title.
        # if thumbnail is not present, show a number instead

        # padding between items
        item_padding_h = 10
        item_padding_v = 10

        surface = pygame.Surface(surface_size)
        surface_w, surface_h = surface.get_size()

        # generate grid layout
        n_items = playlist.length()
        n_cols = math.ceil(math.sqrt(n_items))
        n_rows = math.ceil(n_items / n_cols)
        n_rows = int(n_rows)
        n_cols = int(n_cols)
        item_width = surface_w / n_cols
        item_height = surface_h / n_rows

        # draw selection menu
        item_regions = list()
        for i, col, row, movie in grid_enumerate(playlist._movies, n_cols, start=1):
            left = col * item_width
            top = row * item_height

            # bounding box for this selection menu item
            item_rect = pygame.rect.Rect(
                left + item_padding_h,
                top + item_padding_v,
                item_width - item_padding_h * 2,
                item_height - item_padding_v * 2
            )
            # remember bounding box so we can later resolve mouse clicks
            item_regions.append((item_rect, movie))
            # add gray border
            pygame.draw.rect(surface, (173, 178, 186), item_rect, 1)

            # add some margin within border
            item_rect.inflate_ip(-5, -5)

            # If title is present, render this at bottom of bounding box
            lbl_title_h = 0
            lbl_title_padding_top = 10
            if movie.title:
                tile_font_size = 50
                lbl_title = self._render_text(movie.title, font=pygame.font.Font(None, tile_font_size))
                lbl_title_w, lbl_title_h = lbl_title.get_size()

                # resize label
                if lbl_title_w > item_rect.width:
                    lbl_title = aspect_scale(lbl_title, item_rect.inflate(-5, -5).size, smooth=False)
                    lbl_title_w, lbl_title_h = lbl_title.get_size()

                surface.blit(lbl_title, (item_rect.centerx - lbl_title_w / 2, item_rect.bottom - lbl_title_h))

            # if thumbnail is present, show above title
            if movie.thumbnail:
                lbl_thumb_max_width = item_rect.width - 5
                lbl_thumb_max_height = item_rect.height - lbl_title_h - lbl_title_padding_top - 5

                # scale image to fit bounding box (and also above title, if present)
                # this replaces the image im memory, so we only have to to this once
                lbl_thumb = movie.thumbnail
                lbl_thumb_w, lbl_thumb_h = lbl_thumb.get_size()
                if lbl_thumb_w > lbl_thumb_max_width or lbl_thumb_h > lbl_thumb_max_height:
                    lbl_thumb = aspect_scale(lbl_thumb, (lbl_thumb_max_width, lbl_thumb_max_height))
                    lbl_thumb_w, lbl_thumb_h = lbl_thumb.get_size()
            # if no thumbnail is present, just show a number
            else:
                lbl_thumb = self._render_text(str(i), font=self._big_font)
                lbl_thumb_w, lbl_thumb_h = lbl_thumb.get_size()
            surface.blit(lbl_thumb, (item_rect.centerx - lbl_thumb_w / 2, item_rect.centery - lbl_title_h / 2 - lbl_thumb_h / 2))

        self.__video_selection_regions = item_regions
        return surface

    def _render_selection_menu(self, playlist):
        """Print idle message with video selection"""
        self._print('Rendering menu')
        menu_surface = pygame.Surface(self._screen.get_size())
        menu_surface_w, menu_surface_h = menu_surface.get_size()

        # clear screen
        menu_surface.fill(self._bgcolor)

        if not playlist.length():
            idle_msg = self._reader.idle_message()
            self._print(idle_msg)
            lbl_idle = self._render_text(idle_msg)
            lbl_idle_w, lbl_idle_h = lbl_idle.get_size()
            menu_surface.blit(lbl_idle, (menu_surface_w / 2 - lbl_idle_w / 2, menu_surface_h / 2 - lbl_idle_h / 2))
            return menu_surface

        # Title
        try:
            title_text = self._config.get('video_looper', 'kiosk_title').strip()
        except ConfigParser.NoOptionError:
            title_text = 'Please select a movie'
        lbl_title = self._render_text(title_text)
        lbl_title_w, lbl_title_h = lbl_title.get_size()
        lbl_title_top = 10
        menu_surface.blit(lbl_title, (menu_surface_w / 2 - lbl_title_w / 2, lbl_title_top))

        screen_content_top = lbl_title_top + lbl_title_h
        screen_content_bottom = menu_surface_h
        # If keyboard control is enabled, display message about it
        if self._keyboard_control:
            lbl_exit_help = self._render_text('press ESC to quit', font=pygame.font.Font(None, 16))
            lbl_exit_help_w, lbl_exit_help_h = lbl_exit_help.get_size()
            lbl_exit_help_right = 20
            lbl_exit_help_bottom = 10
            screen_content_bottom = menu_surface_h - lbl_exit_help_h - lbl_exit_help_bottom
            menu_surface.blit(lbl_exit_help, (menu_surface_w - lbl_exit_help_w - lbl_exit_help_right, screen_content_bottom))

        playlist_margin_h = 20
        playlist_margin_v = 10

        # bounding box for video selection
        playlist_surface_w = menu_surface_w - playlist_margin_h - playlist_margin_h
        playlist_surface_h = screen_content_bottom - playlist_margin_v - screen_content_top - playlist_margin_v
        playlist_surface = self.__render_playlist_items(playlist, (playlist_surface_w, playlist_surface_h))
        menu_surface.blit(playlist_surface, (playlist_margin_h, screen_content_top + playlist_margin_v))

        return menu_surface

    def _play_movie(self, movie):
        self._player.stop(1)
        self._print('Playing movie: {0}'.format(movie.path))
        self._player.play(movie.path, loop=False, vol=self._sound_vol)

    def __gpio_start_movie(self, movie):
        def handler(channel, *args, **kwargs):
            # prevent buttons getting pressed to often
            self._print('GPIO event: pin {}'.format(channel))
            if self.__last_gpio_press is not None and (time.time() - self.__last_gpio_press) < 5:
                self._print('Ignoring gpio event...')
                return
            self.__last_gpio_press = time.time()
            self._play_movie(movie)

        return handler

    def _register_gpio_events(self, playlist):
        # https://sourceforge.net/p/raspberry-gpio-python/wiki/BasicUsage/
        if GPIO is None:
            return
        self._print('registering GPIO event handler')

        # reset all pin configurations
        GPIO.cleanup()
        GPIO.setmode(GPIO.BCM)

        used_gpio = set()
        for movie in playlist._movies:
            if not movie.on_gpio:
                continue
            # each IO pin can only be associated with a single movie
            if movie.on_gpio in used_gpio:
                self._print('Duplicate GPIO usage: Pin {} @ {}, Skipping...'.format(movie.on_gpio, movie.path))
                continue
            self._print('Pin {} will start {}'.format(movie.on_gpio, movie))
            GPIO.setup(movie.on_gpio, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # GPIO.cleanup() only resets config, but apparently does not remove existing callbacks.
            # so we manually remove existing callbacks before adding the new one
            GPIO.remove_event_detect(movie.on_gpio)
            GPIO.add_event_detect(
                movie.on_gpio,
                GPIO.RISING,
                callback=self.__gpio_start_movie(movie),
                bouncetime=200
            )
            used_gpio.add(movie.on_gpio)

    def _draw_menu(self):
        self._screen.fill(self._bgcolor)
        self._screen.blit(self.__menu_screen, (0, 0))
        pygame.display.update()

    def _on_playlist_updated(self, new_playlist):
        self._register_gpio_events(new_playlist)
        self.__menu_screen = self._render_selection_menu(new_playlist)
        self._draw_menu()

    def run(self):
        """Main program loop.  Will never return!"""
        # Get playlist of movies to play from file reader.
        playlist = self._build_playlist()
        self._on_playlist_updated(playlist)

        # Main loop to play videos in the playlist and listen for file changes.
        while self._running:
            # Check for changes in the file search path (like USB drives added)
            # and rebuild the playlist.
            if self._reader.is_changed():
                self._player.stop(3)  # Up to 3 second delay waiting for old player to stop.
                # Rebuild playlist and show countdown again (if OSD enabled).
                playlist = self._build_playlist()
                self._on_playlist_updated(playlist)

            # Event handling for key press
            for event in pygame.event.get():
                if event.type == pygame.KEYDOWN and self._keyboard_control:
                    # If pressed key is ESC or Q quit program
                    if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                        self.quit()
                if event.type == pygame.MOUSEBUTTONUP:
                    pos = pygame.mouse.get_pos()
                    if self.__video_selection_regions:
                        for rect, movie in self.__video_selection_regions:
                            if rect.collidepoint(pos):
                                self._play_movie(movie)

            # Give the CPU some time to do other tasks.
            time.sleep(0.1)


if __name__ == '__main__':
    # Default config path to /boot.
    config_path = '/boot/video_looper.ini'
    # Override config path if provided as parameter.
    if len(sys.argv) == 2:
        config_path = sys.argv[1]
    # Create video looper.
    kiosk = VideoKiosk(config_path)
    # Configure signal handlers to quit on TERM or INT signal.
    signal.signal(signal.SIGTERM, kiosk.signal_quit)
    signal.signal(signal.SIGINT, kiosk.signal_quit)
    # Run the main loop.
    kiosk.run()
