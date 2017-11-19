#!/usr/bin/python
# coding: utf-8

# maposmatic, the web front-end of the MapOSMatic city map generation system
# Copyright (C) 2009  David Decotigny
# Copyright (C) 2009  Frédéric Lehobey
# Copyright (C) 2009  David Mentré
# Copyright (C) 2009  Maxime Petazzoni
# Copyright (C) 2009  Thomas Petazzoni
# Copyright (C) 2009  Gaël Utard

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ctypes
import datetime
from PIL import Image
import logging
import multiprocessing
import os
import smtplib
import sys
import threading
import traceback
import subprocess

import ocitysmap
from ocitysmap import renderers
from www.maposmatic.models import MapRenderingJob
from www.settings import ADMINS, OCITYSMAP_CFG_PATH, MEDIA_ROOT
from www.settings import RENDERING_RESULT_PATH, RENDERING_RESULT_FORMATS
from www.settings import DAEMON_ERRORS_SMTP_HOST, DAEMON_ERRORS_SMTP_PORT
from www.settings import DAEMON_ERRORS_SMTP_ENCRYPT
from www.settings import DAEMON_ERRORS_SMTP_USER, DAEMON_ERRORS_SMTP_PASSWORD
from www.settings import DAEMON_ERRORS_EMAIL_FROM
from www.settings import DAEMON_ERRORS_EMAIL_REPLY_TO
from www.settings import DAEMON_ERRORS_JOB_URL

RESULT_SUCCESS = 0
RESULT_KEYBOARD_INTERRUPT = 1
RESULT_PREPARATION_EXCEPTION = 2
RESULT_RENDERING_EXCEPTION = 3
RESULT_TIMEOUT_REACHED = 4

THUMBNAIL_SUFFIX = '_small.png'

EXCEPTION_EMAIL_TEMPLATE = """From: MapOSMatic rendering daemon <%(from)s>
Reply-To: %(replyto)s
To: %(to)s
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit
Subject: Rendering of job #%(jobid)d failed
Date: %(date)s

An error occured while rendering job #%(jobid)d!

%(tb)s

Job information:

%(jobinfo)s

You can view the job page at <%(url)s>.
-- 
MapOSMatic
"""

SUCCESS_EMAIL_TEMPLATE = """From: MapOSMatic rendering daemon <%(from)s>
Reply-To: %(replyto)s
To: %(to)s
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit
Subject: Rendering of job #%(jobid)d succeeded
Date: %(date)s

Hello %(to)s,

your map rendering request for

  %(title)s

has successfully been processed now, and the results can be downloaded
from the rendering jobs detail pages:

  %(url)s

-- 
MapOSMatic"""


FAILURE_EMAIL_TEMPLATE = """From: MapOSMatic rendering daemon <%(from)s>
Reply-To: %(replyto)s
To: %(to)s
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit
Subject: Rendering of job #%(jobid)d failed
Date: %(date)s

Hello %(to)s,

unfortunately your map rendering request for

  %(title)s

has failed.

You can check for failure details on the request detail page:

  %(url)s

-- 
MapOSMatic"""


TIMEOUT_EMAIL_TEMPLATE = """From: MapOSMatic rendering daemon <%(from)s>
Reply-To: %(replyto)s
To: $(to)s
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit
Subject: Rendering of job #%(jobid)d timed out 
Date: %(date)s

Hello %(to)s,

unfortunately your map rendering request for

  %(title)s

has been runnning for more than %(timeout)d minutes and had to be cancelled.

You may want to retry with a smaller map area or with a less complex map
style or less map overlays.

-- 
MapOSMatic"""


l = logging.getLogger('maposmatic')

class ThreadingJobRenderer:
    """
    The ThreadingJobRenderer is a wrapper around a JobRendered thread that
    implements timeout management. If the timeout is reached, the thread is
    suspended, cleaned up and killed.
    """

    def __init__(self, job, timeout=3600, prefix=None):
        """Initializes this ThreadingJobRenderer with a given job and a timeout.

        Args:
            job (MapRenderingJob): the job to render.
            timeout (int): a timeout, in seconds (defaults to 20 minutes).
            prefix (string): renderer map_areas table prefix.
        """

        self.__job = job
        self.__timeout = timeout
        self.__thread = JobRenderer(job, prefix)

    def _email_timeout(self):
        """Send a notification about timeouts to the request submitter"""

        if not DAEMON_ERRORS_SMTP_HOST or not self.__job.submittermail:
            return

        try:
            l.info("Emailing timeout message to %s via %s:%d..." %
                (self.__job.submittermail,
                 DAEMON_ERRORS_SMTP_HOST,
                 DAEMON_ERRORS_SMTP_PORT))

            if DAEMON_ERRORS_SMTP_ENCRYPT == "SSL":
              mailer = smtplib.SMTP_SSL()
            else:
              mailer = smtplib.SMTP()
            mailer.connect(DAEMON_ERRORS_SMTP_HOST, DAEMON_ERRORS_SMTP_PORT)
            if DAEMON_ERRORS_SMTP_ENCRYPT == "TLS":
                mailer.starttls()
            if DAEMON_ERRORS_SMTP_USER and DAEMON_ERRORS_SMTP_PASSWORD:
                mailer.login(DAEMON_ERRORS_SMTP_USER, DAEMON_ERRORS_SMTP_PASSWORD)

            msg = TIMEOUT_EMAIL_TEMPLATE % \
                    { 'from': DAEMON_ERRORS_EMAIL_FROM,
                      'replyto': DAEMON_ERRORS_EMAIL_REPLY_TO,
                      'to': self.__job.submittermail,
                      'jobid': self.__job.id,
                      'date': datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S %Z'),
                      'url': DAEMON_ERRORS_JOB_URL % self.__job.id,
                      'title': self.__job.maptitle,
                      'timeout': self.__timeout / 60
                    }

            mailer.sendmail(DAEMON_ERRORS_EMAIL_FROM,
                    [admin[1] for admin in ADMINS], msg)
            l.info("Email notification sent.")
        except Exception as e:
            l.exception("Could not send notification email to the submitter!")

    def run(self):
        """Renders the job using a JobRendered, encapsulating all processing
        errors and exceptions, with the addition here of a processing timeout.

        Returns one of the RESULT_ constants.
        """

        l.info("Timeout is %d" % self.__timeout)

        self.__thread.start()
        self.__thread.join(self.__timeout)

        # If the thread is no longer alive, the timeout was not reached and all
        # is well.
        if not self.__thread.isAlive():
            if self.__thread.result != 0:
                self.__job.remove_all_files()
            return self.__thread.result

        l.info("Rendering of job #%d took too long (timeout reached)!" %
               self.__job.id)

        # Kill the thread, clean up and return TIMEOUT_REACHED
        self.__thread.kill()
        del self.__thread

        # Remove the job files
        self.__job.remove_all_files()

        self._email_timeout()

        l.debug("Worker removed.")
        return RESULT_TIMEOUT_REACHED


class ForkingJobRenderer:

    def __init__(self, job, timeout=3600, prefix=None):
        self.__job = job
        self.__timeout = timeout
        self.__renderer = JobRenderer(job, prefix)
        self.__process = multiprocessing.Process(target=self._wrap)

    def _email_timeout(self):
        """Send a notification about timeouts to the request submitter"""

        if not DAEMON_ERRORS_SMTP_HOST or not self.__job.submittermail:
            return

        try:
            l.info("Emailing timeout message to %s via %s:%d..." %
                (self.__job.submittermail,
                 DAEMON_ERRORS_SMTP_HOST,
                 DAEMON_ERRORS_SMTP_PORT))

            if DAEMON_ERRORS_SMTP_ENCRYPT == "SSL":
              mailer = smtplib.SMTP_SSL()
            else:
              mailer = smtplib.SMTP()
            mailer.connect(DAEMON_ERRORS_SMTP_HOST, DAEMON_ERRORS_SMTP_PORT)
            if DAEMON_ERRORS_SMTP_ENCRYPT == "TLS":
                mailer.starttls()
            if DAEMON_ERRORS_SMTP_USER and DAEMON_ERRORS_SMTP_PASSWORD:
                mailer.login(DAEMON_ERRORS_SMTP_USER, DAEMON_ERRORS_SMTP_PASSWORD)

            msg = TIMEOUT_EMAIL_TEMPLATE % \
                    { 'from': DAEMON_ERRORS_EMAIL_FROM,
                      'replyto': DAEMON_ERRORS_EMAIL_REPLY_TO,
                      'to': self.__job.submittermail,
                      'jobid': self.__job.id,
                      'date': datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S %Z'),
                      'url': DAEMON_ERRORS_JOB_URL % self.__job.id,
                      'title': self.__job.maptitle,
                      'timeout': self.__timeout / 60
                    }

            mailer.sendmail(DAEMON_ERRORS_EMAIL_FROM,
                    [admin[1] for admin in ADMINS], msg)
            l.info("Email notification sent.")
        except Exception as e:
            l.exception("Could not send notification email to the submitter!")

    def run(self):
        self.__process.start()
        self.__process.join(self.__timeout)

        # If the process is no longer alive, the timeout was not reached and
        # all is well.
        if not self.__process.is_alive():
            if self.__process.exitcode != 0:
                self.__job.remove_all_files()

            # If the exit code is < 0, it means the subprocess was terminated
            # abnormaly (by signal). In this situation, we need to report a
            # rendering exception.
            if self.__process.exitcode >= 0:
                return self.__process.exitcode
            return RESULT_RENDERING_EXCEPTION

        l.info("Rendering of job #%d took too long (timeout reached)!" %
            self.__job.id)

        # Kill the process, clean up and return TIMEOUT_REACHED
        self.__process.terminate()
        del self.__process

        # Remove job files
        self.__job.remove_all_files()

        self._email_timeout()

        l.debug("Process terminated.")
        return RESULT_TIMEOUT_REACHED

    def _wrap(self):
        sys.exit(self.__renderer.run())


class JobRenderer(threading.Thread):
    """
    A simple, blocking job renderer. Can be used as a thread.
    """

    def __init__(self, job, prefix):
        threading.Thread.__init__(self, name='renderer-%d' % job.id)
        self.job = job
        self.prefix = prefix
        self.result = None

    def __get_my_tid(self):
        if not self.isAlive():
            raise threading.ThreadError("the thread is not active")

        # Do we have it cached?
        if hasattr(self, '__thread_id'):
            return self.__thread_id

        # If not, look for it
        for tid, tobj in threading._active.items():
            if tobj is self:
                self.__thread_id = tid
                return self.__thread_id

        raise AssertionError("Could not resolve the thread's ID")

    def kill(self):
        l.debug("Killing job #%d's worker thread..." % self.job.id)
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(self.__get_my_tid(),
                ctypes.py_object(SystemExit))
        if res == 0:
            raise ValueError("Invalid thread ID")
        elif res != 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(self.__get_my_tid(), 0)
            raise SystemError("PyThreadState_SetAsync failed")

    def _email_submitter(self, template):
        """Send a notification with status and result URL to the request submitter"""

        if not DAEMON_ERRORS_SMTP_HOST or not self.job.submittermail:
            return

        try:
            l.info("Emailing success/failure message to %s via %s:%d..." %
                (self.job.submittermail,
                 DAEMON_ERRORS_SMTP_HOST,
                 DAEMON_ERRORS_SMTP_PORT))

            if DAEMON_ERRORS_SMTP_ENCRYPT == "SSL":
              mailer = smtplib.SMTP_SSL()
            else:
              mailer = smtplib.SMTP()
            mailer.connect(DAEMON_ERRORS_SMTP_HOST, DAEMON_ERRORS_SMTP_PORT)
            if DAEMON_ERRORS_SMTP_ENCRYPT == "TLS":
                mailer.starttls()
            if DAEMON_ERRORS_SMTP_USER and DAEMON_ERRORS_SMTP_PASSWORD:
                mailer.login(DAEMON_ERRORS_SMTP_USER, DAEMON_ERRORS_SMTP_PASSWORD)

            msg = template % \
                    { 'from': DAEMON_ERRORS_EMAIL_FROM,
                      'replyto': DAEMON_ERRORS_EMAIL_REPLY_TO,
                      'to': self.job.submittermail,
                      'jobid': self.job.id,
                      'date': datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S %Z'),
                      'url': DAEMON_ERRORS_JOB_URL % self.job.id,
                      'title': self.job.maptitle
                    }

            mailer.sendmail(DAEMON_ERRORS_EMAIL_FROM,
                    [admin[1] for admin in ADMINS], msg)
            l.info("Email notification sent.")
        except Exception as e:
            l.exception("Could not send notification email to the submitter!")


    def _email_exception(self, e):
        """This method can be used to send the given exception by email to the
        configured admins in the project's settings."""

        if not ADMINS or not DAEMON_ERRORS_SMTP_HOST:
            return

        try:
            l.info("Emailing rendering exceptions to the admins (%s) via %s:%d..." %
                (', '.join([admin[1] for admin in ADMINS]),
                 DAEMON_ERRORS_SMTP_HOST,
                 DAEMON_ERRORS_SMTP_PORT))

            if DAEMON_ERRORS_SMTP_ENCRYPT == "SSL":
              mailer = smtplib.SMTP_SSL()
            else:
              mailer = smtplib.SMTP()
            mailer.connect(DAEMON_ERRORS_SMTP_HOST, DAEMON_ERRORS_SMTP_PORT)
            if DAEMON_ERRORS_SMTP_ENCRYPT == "TLS":
                mailer.starttls()
            if DAEMON_ERRORS_SMTP_USER and DAEMON_ERRORS_SMTP_PASSWORD:
                mailer.login(DAEMON_ERRORS_SMTP_USER, DAEMON_ERRORS_SMTP_PASSWORD)

            jobinfo = []
            for k in sorted(self.job.__dict__.keys()):
                # We don't care about state that much, especially since it
                # doesn't display well
                if k != '_state':
                    jobinfo.append('  %s: %s' % (k, str(self.job.__dict__[k])))

            msg = EXCEPTION_EMAIL_TEMPLATE % \
                    { 'from': DAEMON_ERRORS_EMAIL_FROM,
                      'replyto': DAEMON_ERRORS_EMAIL_REPLY_TO,
                      'to': ', '.join(['%s <%s>' % admin for admin in ADMINS]),
                      'jobid': self.job.id,
                      'jobinfo': '\n'.join(jobinfo),
                      'date': datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S %Z'),
                      'url': DAEMON_ERRORS_JOB_URL % self.job.id,
                      'tb': traceback.format_exc(e)
                    }

            mailer.sendmail(DAEMON_ERRORS_EMAIL_FROM,
                    [admin[1] for admin in ADMINS], msg)
            l.info("Error report sent.")
        except Exception as e:
            l.exception("Could not send error email to the admins!")

        self._email_submitter(FAILURE_EMAIL_TEMPLATE)

    def _gen_thumbnail(self, prefix, paper_width_mm, paper_height_mm):
        l.info('Creating map thumbnail...')

        if self.job.layout == "multi_page":
            # Depending on whether we're rendering landscape or
            # portrait, adapt how the tiling is done.
            if paper_width_mm > paper_height_mm:
                tile = "1x2"
            else:
                tile = "2x1"

            # With the 'montage' command from ImageMagick, create an
            # image with the first two pages of the PDF (cover page
            # and overview page).
            montage_cmd = [ "montage", "-tile", tile, "%s.pdf[0]" % prefix,
                            "%s.pdf[2]" % prefix, "-geometry", "+10+10",
                            "-shadow", "%s%s" % (prefix, THUMBNAIL_SUFFIX) ]
            subprocess.check_call(montage_cmd)

            # And now scale it to the normal thumbnail size
            mogrify_cmd = [ "mogrify", "-scale", "200x200",
                            "%s%s" % (prefix, THUMBNAIL_SUFFIX) ]
            subprocess.check_call(mogrify_cmd)

        elif 'png' in RENDERING_RESULT_FORMATS:
                img = Image.open(prefix + '.png')
                img.save(prefix + '.jpg', quality=50)
                img.thumbnail((200, 200), Image.ANTIALIAS)
                img.save(prefix + THUMBNAIL_SUFFIX)
                pngquant_cmd = [ "pngquant", "--output", "%s.8bit.png" % prefix,
                                 "%s.png" % prefix ]
                subprocess.check_call(pngquant_cmd)

    def run(self):
        """Renders the given job, encapsulating all processing errors and
        exceptions.

        This does not affect the job entry in the database in any way. It's the
        responsibility of the caller to do maintain the job status in the
        database.

        Returns one of the RESULT_ constants.
        """

        l.info("Rendering job #%d '%s'..." % (self.job.id, self.job.maptitle))

        try:
            renderer = ocitysmap.OCitySMap(OCITYSMAP_CFG_PATH)
            config = ocitysmap.RenderingConfiguration()
            config.title = self.job.maptitle
            config.osmid = self.job.administrative_osmid

            if config.osmid:
                bbox_wkt, area_wkt \
                    = renderer.get_geographic_info(config.osmid)
                config.bounding_box = ocitysmap.coords.BoundingBox.parse_wkt(
                    bbox_wkt)
            else:
                config.bounding_box = ocitysmap.coords.BoundingBox(
                        self.job.lat_upper_left,
                        self.job.lon_upper_left,
                        self.job.lat_bottom_right,
                        self.job.lon_bottom_right)

            if self.job.track and self.job.track_bbox_mode:
               gpx_bbox = ocitysmap.coords.BoundingBox.parse_gpx(os.path.join(MEDIA_ROOT, self.job.track.name))
               if self.job.track_bbox_mode == 1:
                 # 1 -> merge GPX and map bounding box
                 config.bounding_box.merge(gpx_bbox)
               elif self.job.track_bbox_mode == 2: 
                 # 2 -> replace map bbox with GPX bbox
                 config.bounding_box = gpx_bbox

            config.language = self.job.map_language
            config.stylesheet = renderer.get_stylesheet_by_name(
                self.job.stylesheet)
            config.overlays = []
            if self.job.overlay:
                for overlay in self.job.overlay.split(","):
                    config.overlays.append(renderer.get_overlay_by_name(overlay))
            if self.job.track:
                config.gpx_file = os.path.join(MEDIA_ROOT, self.job.track.name)
            else:
                config.gpx_file = False
                config.track_bbox_mode = 0
            config.paper_width_mm = self.job.paper_width_mm
            config.paper_height_mm = self.job.paper_height_mm
        except KeyboardInterrupt:
            self.result = RESULT_KEYBOARD_INTERRUPT
            l.info("Rendering of job #%d interrupted!" % self.job.id)
            return self.result
        except Exception as e:
            self.result = RESULT_PREPARATION_EXCEPTION
            l.exception("Rendering of job #%d failed (exception occurred during"
                        " data preparation)!" % self.job.id)
            errfile = os.path.join(RENDERING_RESULT_PATH, self.job.files_prefix() + "-errors.txt")
            fp = open(errfile, "w")
            traceback.print_exc(file=fp)
            fp.close()
            self._email_exception(e)
            return self.result

        prefix = os.path.join(RENDERING_RESULT_PATH, self.job.files_prefix())

        try:
            # Get the list of output formats (PNG, PDF, SVGZ, CSV)
            # that the renderer accepts.
            renderer_cls = renderers.get_renderer_class_by_name(self.job.layout)
            compatible_output_formats = renderer_cls.get_compatible_output_formats()

            # Compute the intersection of the accepted output formats
            # with the desired output formats.
            output_formats = \
                list(set(compatible_output_formats) & set(RENDERING_RESULT_FORMATS))

            renderer.render(config, self.job.layout,
                            output_formats, prefix)

            # Create thumbnail
            self._gen_thumbnail(prefix, config.paper_width_mm,
                                config.paper_height_mm)

            self.result = RESULT_SUCCESS
            l.info("Finished rendering of job #%d." % self.job.id)
        except KeyboardInterrupt:
            self.result = RESULT_KEYBOARD_INTERRUPT
            l.info("Rendering of job #%d interrupted!" % self.job.id)
            return self.result
        except Exception as e:
            self.result = RESULT_RENDERING_EXCEPTION
            l.exception("Rendering of job #%d failed (exception occurred during"
                        " rendering)!" % self.job.id)
            errfile = os.path.join(RENDERING_RESULT_PATH, self.job.files_prefix() + "-errors.txt")
            fp = open(errfile, "w")
            traceback.print_exc(file=fp)
            fp.close()
            self._email_exception(e)
            return self.result

        self._email_submitter(SUCCESS_EMAIL_TEMPLATE)

        return self.result


if __name__ == '__main__':
    def usage():
        sys.stderr.write('usage: %s <jobid> [timeout]\n' % sys.argv[0])

    if len(sys.argv) < 2 or len(sys.argv) > 3:
        usage()
        sys.exit(3)

    try:
        jobid = int(sys.argv[1])
        job = MapRenderingJob.objects.get(id=jobid)

        if job:
            prefix = 'renderer_%d_' % os.getpid()
            if len(sys.argv) == 3:
                renderer = ThreadingJobRenderer(job, int(sys.argv[2]), prefix)
            else:
                renderer = JobRenderer(job, prefix)

            sys.exit(renderer.run())
        else:
            sys.stderr.write('Job #%d not found!' % jobid)
            sys.exit(4)
    except ValueError:
        usage()
        sys.exit(3)

