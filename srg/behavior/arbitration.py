"""

This file is part of FINITE STATE MACHINE BASED TESTING.

Copyright(c) <Florian Lier, Simon Schulz>
http://opensource.cit-ec.de/fsmt

This file may be licensed under the terms of the
GNU Lesser General Public License Version 3 (the ``LGPL''),
or (at your option) any later version.

Software distributed under the License is distributed
on an ``AS IS'' basis, WITHOUT WARRANTY OF ANY KIND, either
express or implied. See the LGPL for the specific language
governing rights and limitations.

You should have received a copy of the LGPL along with this
program. If not, go to http://www.gnu.org/licenses/lgpl.html
or write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

The development of this software was supported by the
Excellence Cluster EXC 277 Cognitive Interaction Technology.
The Excellence Cluster EXC 277 is a grant of the Deutsche
Forschungsgemeinschaft (DFG) in the context of the German
Excellence Initiative.

Authors: Florian Lier, Simon Schulz
<flier, sschulz>@techfak.uni-bielefeld.de

"""

# STD IMPORTS
import sys
import time
import yaml
import threading

# SELF IMPORTS
from srg.robot import driver as d
from srg.control import gaze as g
from srg.middleware import ros as r
from srg.utils import transform as t


class Arbitration(threading.Thread):

    def __init__(self, _configfile, _outscope):
        threading.Thread.__init__(self)
        self.lock             = threading.RLock()
        self.run_toggle       = True
        self.cfgfile          = _configfile.strip()
        self.outscope         = _outscope
        self.last_info        = time.time()
        self.transforms       = []
        self.input_sources    = []
        self.gaze_controller  = []
        self.overrides        = []
        self.boring              = None
        self.config              = None
        self.winner              = None
        self.arbitrate_toggle    = None
        self.rd                  = None
        self.allow_peak_override = None
        self.loop_speed          = 1.0

    def boot_robot_driver(self):
        self.rd = d.RobotDriver("ROS", self.outscope.strip())

    def configure(self):
        self.read_yaml_config()
        self.configure_middleware()

    def read_yaml_config(self):
        try:
            print "---"
            print ">>> Using config: %s" % self.cfgfile
            print "---"
            f = open(self.cfgfile)
            config_vals = yaml.load(f)
            self.config = config_vals
            f.close()
            if len(self.config["resolution"]) != len(self.config["priorities"]) or len(self.config["resolution"]) != len(self.config["fov"]):
                print ">>> Please check your config file, not enough values provided..."
                sys.exit(1)
            else:
                pass
        except Exception, e:
            print ">>> %s" % str(e)
            sys.exit(1)

    def configure_middleware(self):
        # Start the external control MW Thread
        self.arbitrate_toggle = r.RosControlConnector()
        self.arbitrate_toggle.start()

        # Read config file an extract values
        idx = 0
        for item in self.config["priorities"]:
            res        = self.config["resolution"][idx].split("x")
            fov        = self.config["fov"][idx].split("x")
            datatypes  = self.config["datatypes"][idx].split(":")
            modes      = self.config["modes"][idx]
            stimulus_timeout = self.config["stimulus_timeout"][idx]
            peak_override    = int(self.config["allow_peak_override"][0])
            self.boring      = float(self.config["boring_timeout"][0])

            # Check whether peak_override is "ON" (1)
            if peak_override is 1:
                self.allow_peak_override = peak_override
                allow_override_threshold = self.config["peak_overrides"][idx]
                self.overrides.append(allow_override_threshold)

            # Configure Affine Transformations
            at = t.AffineTransform(str(item))
            at.set_coords(float(res[0]), float(res[1]), float(fov[0]), float(fov[1]))
            at.calculate_divider()
            self.transforms.append(at)

            # Configure Middleware Adapters
            if datatypes[0].lower() == "ros":
                mw = r.RosConnector(str(item), at, datatypes[1], modes, stimulus_timeout, self.lock)
            elif datatypes[0].lower() == "rsb":
                print ">>> RSB is currrenly not supported :| "
                self.run_toggle = False
                sys.exit(1)
            else:
                print ">>> Unknown middleware %s" % datatypes[0]
                self.run_toggle = False
                sys.exit(1)
            self.input_sources.append(mw)

            # Configure Gaze Controllers
            gc = g.GazeController(self.rd, mw, self.lock)
            self.gaze_controller.append(gc)
            idx += 1

        # RUN EVERYTHING!
        for i_s in self.input_sources:
            i_s.start()
        for g_c in self.gaze_controller:
            g_c.start()

    def request_stop(self):
        for connection in self.input_sources:
            connection.run_toggle = False
        for gazecontrol in self.gaze_controller:
            gazecontrol.run_toggle = False
        self.arbitrate_toggle.run_toggle = False
        self.run_toggle = False

    def get_latest_targets(self):
        updates = []
        stimulus_timeouts = []
        current_gaze_values = []
        for target in self.input_sources:
            if target.current_robot_gaze is not None:
                updates.append(target.current_robot_gaze_timestamp)
                stimulus_timeouts.append(target.stimulus_timeout)
                current_gaze_values.append(target)
            else:
                updates.append(None)
                stimulus_timeouts.append(target.stimulus_timeout)
                current_gaze_values.append(None)
        self.derive_order(updates, stimulus_timeouts, current_gaze_values)

    def derive_order(self, _updates, _stimulus_timeouts, _current_gaze_values):
        winner = 0
        idx = -1
        n = -1
        p = -1
        override = False
        now = time.time()
        if len(_current_gaze_values) != len(_stimulus_timeouts) or len(_current_gaze_values) != len(_updates):
            print ">>> Waiting for data..."
            return
        # Now honor priority and latest input
        if self.allow_peak_override is not None:
            if len(_current_gaze_values) != len(self.overrides) or len(_current_gaze_values) != len(_stimulus_timeouts):
                print ">>> Waiting for data in override mode..."
                return
            for stamp_override in _updates:
                p += 1
                if stamp_override is not None:
                    if _current_gaze_values[p].datatype.lower() == "people":
                        if int(_current_gaze_values[p].nearest_person_z) > int(self.overrides[p]) and now - stamp_override <= _stimulus_timeouts[p] + self.boring:
                            print ">>> Override %s" % _current_gaze_values[p].datatype.lower()
                            idx += 1
                            winner = idx
                            override = True
                            break
                    if _current_gaze_values[p].datatype.lower() == "pointstamped":
                        if int(_current_gaze_values[p].point_z) < int(self.overrides[p]) and now - stamp_override <= _stimulus_timeouts[p] + self.boring:
                            print ">>> Override %s" % _current_gaze_values[p].datatype.lower()
                            idx += 1
                            winner = idx
                            override = True
                            break
        if override is False:
            for stamp in _updates:
                n += 1
                if stamp is not None:
                    if now - stamp <= _stimulus_timeouts[n] + self.boring:
                        idx += 1
                        winner = idx
                        break
                    else:
                        # Too boring advance in prios
                        idx += 1
                else:
                    # Next prio, because we don't have any values yet.
                    idx += 1
        # Now enable the correct gaze controller
        idx = 0
        for gz in self.gaze_controller:
            if idx == winner:
                gz.acquire_prio = True
                now = time.time()
                self.winner = winner
                if now - self.last_info >= 1.0:
                    print ">>> Winning input is %s" % self.input_sources[winner].inscope
                    self.last_info = time.time()
            else:
                gz.acquire_prio = False
            idx += 1

    def run(self):
        while self.run_toggle:
            now = time.time()
            if self.arbitrate_toggle.pause_auto_arbitrate is False:
                self.lock.acquire()
                self.get_latest_targets()
                self.lock.release()
            else:
                for gz in self.gaze_controller:
                    gz.acquire_prio = False
            hz = 0.01
            # Running with maximum frequency of 100 Hz
            time.sleep(hz)
            then = time.time()
            duration = then - now
            self.loop_speed = 1000/duration
        print ">>> Stopping Arbitration"
