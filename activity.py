# Copyright 2013 Agustin Zubiaga <aguz@sugarlabs.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import logging
from gettext import gettext as _

from gi.repository import GObject
GObject.threads_init()
from gi.repository import Gtk
from gi.repository import GConf

import dbus
import os.path
import json

import avahi
from dbus.mainloop.glib import DBusGMainLoop
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import SocketServer
import socket
import select
import errno
import urllib2
import urllib
from urlparse import urlparse, parse_qs

from sugar3.activity import activity
from sugar3.activity.widgets import ActivityToolbarButton
from sugar3.activity.widgets import StopButton
from sugar3.graphics.toolbarbox import ToolbarBox
from sugar3.graphics.toggletoolbutton import ToggleToolButton
from sugar3 import profile

#import server
#import utils

JOURNAL_STREAM_SERVICE = 'journal-activity-http'

# directory exists if powerd is running.  create a file here,
# named after our pid, to inhibit suspend.
POWERD_INHIBIT_DIR = '/var/run/powerd-inhibit-suspend'


class ZeroconfService:
    """A simple class to publish a network service with zeroconf using
    avahi.

    """

    def __init__(self, name, port, stype="_http._tcp",
                 domain="", host="", text=""):
        self.name = name
        self.stype = stype
        self.domain = domain
        self.host = host
        self.port = port
        self.text = text

    def publish(self):
        bus = dbus.SystemBus()
        server = dbus.Interface(
            bus.get_object(avahi.DBUS_NAME, avahi.DBUS_PATH_SERVER),
            avahi.DBUS_INTERFACE_SERVER)

        g = dbus.Interface(
            bus.get_object(avahi.DBUS_NAME, server.EntryGroupNew()),
            avahi.DBUS_INTERFACE_ENTRY_GROUP)

        g.AddService(avahi.IF_UNSPEC, avahi.PROTO_UNSPEC, dbus.UInt32(0),
                     self.name, self.stype, self.domain, self.host,
                     dbus.UInt16(self.port), self.text)

        g.Commit()
        self.group = g

    def unpublish(self):
        self.group.Reset()


class TeacherRequestHandler(BaseHTTPRequestHandler):

    def __init__(self, received_data_cb, request, client_address, server):
        self._received_data_cb = received_data_cb
        BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    #Handler for the GET requests
    def do_GET(self):
        logging.error('do_GET path: %s', self.path)
        student_data = parse_qs(urlparse(self.path).query)
        GObject.idle_add(self._received_data_cb, student_data)

        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send the html message
        teacher_data = {'nick_name': profile.get_nick_name()}
        self.wfile.write(json.dumps(teacher_data))
        return


class MyHTTPServer(HTTPServer):

    def serve_forever(self, poll_interval=0.5):
        """Overridden version of BaseServer.serve_forever that does not fail
        to work when EINTR is received.
        """
        self._BaseServer__serving = True
        self._BaseServer__is_shut_down.clear()
        while self._BaseServer__serving:

            # XXX: Consider using another file descriptor or
            # connecting to the socket to wake this up instead of
            # polling. Polling reduces our responsiveness to a
            # shutdown request and wastes cpu at all other times.
            try:
                r, w, e = select.select([self], [], [], poll_interval)
            except select.error, e:
                if e[0] == errno.EINTR:
                    logging.debug("got eintr")
                    continue
                raise
            if r:
                self._handle_request_noblock()
        self._BaseServer__is_shut_down.set()

    def server_bind(self):
        """Override server_bind in HTTPServer to not use
        getfqdn to get the server name because is very slow."""
        SocketServer.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        self.server_name = 'localhost'
        self.server_port = port


class ClassroomDiscover(activity.Activity):

    def __init__(self, handle):

        activity.Activity.__init__(self, handle)

        # avahi initialization
        self._service = None

        toolbar_box = ToolbarBox()

        self._vbox = Gtk.VBox()

        activity_button = ActivityToolbarButton(self)
        toolbar_box.toolbar.insert(activity_button, 0)
        activity_button.show()

        # get information from gconf
        client = GConf.Client.get_default()
        self._age = client.get_int('/desktop/sugar/user/age')
        self._gender = client.get_string('/desktop/sugar/user/gender')
        if self._gender is None:
            self._gender = 'male'
        teacher = (self._age >= 25)

        # if age is not configured age == 0

        if teacher or self._age == 0:
            teacher_button = ToggleToolButton('%s-7' % self._gender)
            teacher_button.set_tooltip(_('Teacher'))
            teacher_button.show()
            teacher_button.connect('toggled', self.__start_teacher_cb)
            toolbar_box.toolbar.insert(teacher_button, -1)
            if teacher:
                teacher_button.set_active(True)

        if not teacher or self._age == 0:
            student_button = ToggleToolButton('%s-2' % self._gender)
            student_button.set_tooltip(_('Student'))
            student_button.show()
            student_button.connect('toggled', self.__start_student_cb)
            toolbar_box.toolbar.insert(student_button, -1)
            if self._age > 0:
                # is a student
                student_button.set_active(True)

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = False
        separator.set_expand(True)
        separator.show()
        toolbar_box.toolbar.insert(separator, -1)

        stopbutton = StopButton(self)
        toolbar_box.toolbar.insert(stopbutton, -1)
        stopbutton.show()

        self.set_toolbar_box(toolbar_box)
        toolbar_box.show()

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.add_with_viewport(self._vbox)

        self.scrolled.show_all()
        self.set_canvas(self.scrolled)

        self._inhibit_suspend()

    def _joined_cb(self, also_self):
        """Callback for when a shared activity is joined.
        Get the shared tube from another participant.
        """
        self.watch_for_tubes()
        GObject.idle_add(self._get_view_information)

    def _show_received_student_info(self, student_data):
        logging.error('received data %s', student_data)
        hbox = Gtk.HBox()

        label_n = Gtk.Label()
        label_n.set_text('Name: %s' % student_data['nick_name'][0])
        hbox.add(label_n)

        label_a = Gtk.Label()
        label_a.set_text('Age: %s' % student_data['age'][0])
        hbox.add(label_a)

        label_g = Gtk.Label()
        label_g.set_text('Gender: %s' % student_data['gender'][0])
        hbox.add(label_g)

        hbox.show_all()
        self._vbox.pack_start(hbox, False, False, 10)
        logging.error('added to the canvas')

    def __start_teacher_cb(self, button=None):
        if self._service is None:

            # get a free port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
            sock.bind(('', 0))
            sock.listen(socket.SOMAXCONN)
            _ipaddr, self.port = sock.getsockname()
            sock.shutdown(socket.SHUT_RDWR)
            logging.error('Using port %d', self.port)

            # puvblish the server direction
            self._service = ZeroconfService(name="Teacher", port=self.port,
                                            text=profile.get_nick_name())
            logging.error('Publish teacher zeroconf service')
            self._service.publish()

            # start the http server
            httpd = MyHTTPServer(('', self.port),
                                 lambda *args: TeacherRequestHandler(
                                 self._show_received_student_info, *args))

            from threading import Thread
            self._server = Thread(target=httpd.serve_forever)
            self._server.setDaemon(True)
            self._server.start()
            logging.debug("After start server")

        else:
            logging.error('Unpublish teacher zeroconf service')
            self._service.unpublish()
            self._service = None
            self._server.stop()

    def __start_student_cb(self, button=None):

        TYPE = "_http._tcp"

        loop = DBusGMainLoop()
        bus = dbus.SystemBus(mainloop=loop)

        self._server = dbus.Interface(bus.get_object(avahi.DBUS_NAME, '/'),
                                      'org.freedesktop.Avahi.Server')

        sbrowser = dbus.Interface(
            bus.get_object(avahi.DBUS_NAME, self._server.ServiceBrowserNew(
                avahi.IF_UNSPEC, avahi.PROTO_UNSPEC, TYPE, 'local',
                dbus.UInt32(0))),
            avahi.DBUS_INTERFACE_SERVICE_BROWSER)

        sbrowser.connect_to_signal("ItemNew", self._new_zconf_item_handler)

    def _new_zconf_item_handler(self, interface, protocol, name, stype,
                                domain, flags):
        print "Found service '%s' type '%s' domain '%s' " % (name, stype,
                                                             domain)

        if flags & avahi.LOOKUP_RESULT_LOCAL:
            # local service, skip
            pass

        if name == "Teacher":
            self._server.ResolveService(
                interface, protocol, name, stype, domain, avahi.PROTO_UNSPEC,
                dbus.UInt32(0), reply_handler=self._service_resolved,
                error_handler=self._print_error)

    def _service_resolved(self, *args):
        logging.error('service resolved name: %s address %s port %s more %s',
                      args[2], args[7], args[8], args)

        nick_name = profile.get_nick_name()
        teacher_ip = args[7]
        teacher_port = args[8]
        teacher_xo_id = args[5]

        for widget in self._vbox.get_children():
            self._vbox.remove(widget)

        label = Gtk.Label()
        self._vbox.add(label)
        text = ("My name is %s \n" % nick_name)
        label.set_text(text)

        label = Gtk.Label()
        self._vbox.add(label)
        text = ("Teacher ip: %s \n" % teacher_ip)
        label.set_text(text)

        label = Gtk.Label()
        self._vbox.add(label)
        text = ("Teacher port: %s \n" % teacher_port)
        label.set_text(text)

        label = Gtk.Label()
        self._vbox.add(label)
        text = ("Teacher xo id: %s \n" % teacher_xo_id)
        label.set_text(text)
        self._vbox.show_all()

        # sent my information to the teacher
        student_data = {}
        student_data['nick_name'] = nick_name
        student_data['age'] = self._age
        student_data['gender'] = self._gender

        url_values = urllib.urlencode(student_data)

        response = urllib2.urlopen(
            'http://%s:%d/student_info?%s' % (teacher_ip, teacher_port,
                                              url_values))
        json_data = response.read()
        teacher_data = json.loads(json_data)

        label = Gtk.Label()
        self._vbox.add(label)
        text = ("Teacher name: %s \n" % teacher_data['nick_name'])
        label.set_text(text)
        label.show()

    def _print_error(self, *args):
        logging.error('error_handler %s', args[0])

    def read_file(self, file_path):
        pass

    def write_file(self, file_path):
        pass

    def can_close(self):
        self._allow_suspend()
        return True

    # power management (almost copied from clock activity)

    def powerd_running(self):
        return os.access(POWERD_INHIBIT_DIR, os.W_OK)

    def _inhibit_suspend(self):
        if self.powerd_running():
            fd = open(POWERD_INHIBIT_DIR + "/%u" % os.getpid(), 'w')
            fd.close()
            return True
        else:
            return False

    def _allow_suspend(self):
        if self.powerd_running():
            if os.path.exists(POWERD_INHIBIT_DIR + "/%u" % os.getpid()):
                os.unlink(POWERD_INHIBIT_DIR + "/%u" % os.getpid())
            return True
        else:
            return False
