# -*- coding: utf-8 -*-
"""The short schedule beside a Silo-style skin's Live TV quick guide.

As the quick guide's focus moves down the channels, Kodi started this add-on
as a plugin for each channel's schedule: on a SHIELD, starting Python took
most of a second a channel, though the guide answers in a tenth. Instead the
service, already running, watches the quick guide while it's open and
publishes the focused channel's schedule as Home window properties:

    silokodi.quick.<n>.label / .label2 / .<property>   (n from 1 to ROWS)
    silokodi.quick.count, silokodi.quick.for (the channel number they're for)

for the skin's static rows. Nothing happens while the quick guide is closed
(it never opens without Live TV)."""
import time

import xbmc

from . import channels, kodi

ROWS = 15
PREFIX = 'quick.'
GUIDE_OPEN = 'Window.IsActive(pvrosdchannels)'
FOCUSED = 'Container(11).ListItem.%s'
CHANNELS_FOR = 300            # seconds the channel list is kept
SCHEDULE_FOR = 30             # and each channel's schedule, republished as often (what's on now moves)


class QuickGuide(object):
    """Publishes the focused channel's schedule while the quick guide is
    open. watch() runs in a thread of the service."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.channels, self.channels_at = None, 0
        self.schedules = {}       # channel id -> (when read, rows)
        self.shown = None         # the channel published
        self.shown_at = 0
        self.rows = 0

    def watch(self):
        while not self.monitor.waitForAbort(0.2):
            if not xbmc.getCondVisibility(GUIDE_OPEN):
                self.shown = None
                continue
            number = xbmc.getInfoLabel(FOCUSED % 'ChannelNumberLabel')
            name = xbmc.getInfoLabel(FOCUSED % 'ChannelName')
            if not (number or name):
                continue
            if (number, name) == self.shown and time.time() - self.shown_at < SCHEDULE_FOR:
                continue
            try:
                self.publish(number, name)
            except Exception as error:  # never let it stop the service
                kodi.log_warning('Quick guide: %s' % error)
            self.shown, self.shown_at = (number, name), time.time()

    def channel(self, number, name):
        if self.channels is None or time.time() - self.channels_at > CHANNELS_FOR:
            self.channels = (kodi.jsonrpc('PVR.GetChannels', channelgroupid='alltv',
                                          properties=['channelnumber', 'hasarchive']) or {}).get('channels') or []
            self.channels_at = time.time()
        return channels.find_channel(number, name, self.channels)

    def schedule(self, channel):
        when, rows = self.schedules.get(channel['channelid'], (0, None))
        if rows is None or time.time() - when > SCHEDULE_FOR:
            rows = channels.channel_schedule(channel)
            self.schedules[channel['channelid']] = (time.time(), rows)
        return channels.around_now(rows)

    def publish(self, number, name):
        channel = self.channel(number, name)
        rows = self.schedule(channel) if channel else []
        clock = xbmc.getRegion('time').replace(':%S', '')
        found = channels.items(rows, channel, clock)[:ROWS] if channel else []
        values = {}
        for n, (label, label2, props) in enumerate(found, 1):
            values['%s%d.label' % (PREFIX, n)] = label
            values['%s%d.label2' % (PREFIX, n)] = label2
            for key in ('silo.header', 'silo.now', 'silo.archive', 'silo.today'):
                values['%s%d.%s' % (PREFIX, n, key)] = props.get(key, '')
        for n in range(len(found) + 1, max(self.rows, len(found)) + 1):
            for key in ('label', 'label2', 'silo.header', 'silo.now', 'silo.archive', 'silo.today'):
                values['%s%d.%s' % (PREFIX, n, key)] = ''
        values[PREFIX + 'count'] = str(len(found))
        values[PREFIX + 'for'] = number
        for key, value in values.items():
            kodi.set_property(key, value)
        self.rows = len(found)
