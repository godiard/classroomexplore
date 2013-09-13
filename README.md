classroomexplore
================

This Sugar activity is a proof of concept about
a mechanism to auto-discover teacher/kids in a classroom.

Is based in features, like user age and gender configuration available 
in image for Australia.

We need that information by example if we want distribute information
from the teacher to the kids in a automatic way, and can be useful
to distribute other data, as configurations.

The implementation use avahi to allow discovering
and a simple http server to transfer the information.
Right now, only nick name, age and gender is transferred.

Avahi is already included in our xos, and is used in other contexts,
in F18 a feature is dedicated to it.

The activity use the configuration used by the age selector to detect
if the xo is from a student or teacher (adult==teacher)

The teacher xo, detect a free port, start a web server, and publish
the service using avahi.

The kids xo, receive the information about the server, and send
his own information to the teacher.

You can test avahi on the command line using
In the server:
avahi-publish -s Teacher _http._tcp  8100 192.168.0.5
In the client:
avahi-browse -a
avahi-browse -ar
(if not installed, install avahi-tools)

The python bindings are included in the package avahi-ui-tools,
and there are already a bug to package them in another package [2]
but really are a few files with constants definitions,
is almost all done using dbus. I have copied these files inside the activity,
then, we don't need install anything more.

The information is not saved, just displayed.

To test it, remember use one xo with a adult configuration, and
another with a kid config. In your desktop can fail depending on the
firewall.

Gonzalo

[1] http://fedoraproject.org/wiki/Features/AvahiDefaultOnDesktop
[2] https://bugzilla.redhat.com/show_bug.cgi?id=189399
