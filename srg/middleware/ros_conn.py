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
import time
import operator
import threading

# ROS IMPORTS
import rospy
import roslib
from std_msgs.msg import Header
from std_msgs.msg import Bool
from people_msgs.msg import Person
from people_msgs.msg import People
from geometry_msgs.msg import PointStamped

# HLRC IMPORTS
from hlrc_client import RobotGaze
from hlrc_client import RobotTimestamp


class ROSPauseConnector(threading.Thread):

    def __init__(self, _prefix, _paused, _lock):
        threading.Thread.__init__(self)
        self.prefix     = "/"+str(_prefix.lower().strip())
        self.paused      = _paused
        self.is_paused  = False
        self.lock       = _lock
        self.run_toggle = True
        self.pub_setpause = rospy.Publisher(self.prefix+"/robotgazetools/set/pause", Bool, queue_size=1)
        self.pub_getpause = rospy.Publisher(self.prefix+"/robotgazetools/get/pause", Bool, queue_size=1)
        self.p = True
        self.r = False
        self.rate = rospy.Rate(50)

    def pause(self):
        self.pub_setpause.publish(self.p)

    def resume(self):
        self.pub_setpause.publish(self.r)

    def run(self):
        while self.run_toggle is True:
            self.lock.acquire()
            self.pub_getpause.publish(self.paused.get_paused())
            self.is_paused = self.paused.get_paused()
            self.lock.release()
            self.rate.sleep()


class ROSControlConnector(threading.Thread):
    def __init__(self, _prefix, _paused, _lock):
        threading.Thread.__init__(self)
        self.run_toggle = True
        self.ready = False
        self.lock = _lock
        self.paused = _paused
        self.prefix = "/"+str(_prefix.lower().strip())
        self.inscope = self.prefix+"/robotgazetools/set/pause"

    def control_callback(self, ros_data):
        if ros_data.data is True:
            self.lock.acquire()
            self.paused.set_pause()
            self.lock.release()
            print ">>> Auto Arbitrate is PAUSED (ROS)"
        else:
            self.lock.acquire()
            self.paused.set_resume()
            self.lock.release()
            print ">>> Auto Arbitrate is RESUMED (ROS)"

    def run(self):
        print ">>> Initializing ROS Status Subscriber to: %s" % self.inscope.strip()
        toggle_subscriber = rospy.Subscriber(self.inscope, Bool, self.control_callback, queue_size=1)
        self.ready = True
        while self.run_toggle is True:
            time.sleep(0.05)
        toggle_subscriber.unregister()
        print ">>> Deactivating ROS Status Subscriber to: %s" % self.inscope.strip()


class ROSDataConnector(threading.Thread):
    """
    The GazeController receives person messages (ROS) and derives
    the nearest person identified. Based on this, the robot's
    joint angle target's are derived using the transformation
    class below
    """
    def __init__(self, _inscope, _transform, _datatype, _mode, _stimulus_timeout, _lock):
        threading.Thread.__init__(self)
        self.lock       = _lock
        self.run_toggle = True
        self.ready    = False
        self.trans    = _transform
        self.inscope  = str(_inscope).lower().strip()
        self.datatype = str(_datatype).lower().strip()
        self.mode     = str(_mode).lower().strip()
        self.stimulus_timeout = float(_stimulus_timeout)
        self.nearest_person_x = 0.0
        self.nearest_person_y = 0.0
        self.nearest_person_z = 0.0
        self.roi_x            = 0.0
        self.roi_y            = 0.0
        self.point_x          = 0.0
        self.point_y          = 0.0
        self.point_z          = 0.0
        self.current_robot_gaze = None
        self.current_robot_gaze_timestamp = None

    def people_callback(self, ros_data):
        self.lock.acquire()
        send_time = ros_data.header.stamp
        idx = -1
        max_distance = {}
        for person in ros_data.people:
            idx += 1
            max_distance[str(idx)] = person.position.z
        # print ">> Persons found {idx, distance}: ", max_distance
        sort = sorted(max_distance.items(), key=operator.itemgetter(1), reverse=True)
        # print ">> Nearest Face: ", sort
        # print ">> Index: ", sort[0][0]
        # print ">> Distance in pixels: ", sort[0][1]
        self.nearest_person_x = ros_data.people[int(sort[0][0])].position.x
        self.nearest_person_y = ros_data.people[int(sort[0][0])].position.y
        self.nearest_person_z = ros_data.people[int(sort[0][0])].position.z
        # print ">> Position in pixels x:", self.nearest_person_x
        # print ">> Position in pixels y:", self.nearest_person_y
        point = [self.nearest_person_x, self.nearest_person_y]
        # Derive coordinate mapping
        angles = self.trans.derive_mapping_coords(point)
        if angles is not None:
            g = RobotGaze()
            if self.mode == 'relative':
                g.gaze_type = RobotGaze.GAZETARGET_RELATIVE
            else:
                g.gaze_type = RobotGaze.GAZETARGET_ABSOLUTE
            self.current_robot_gaze_timestamp = send_time.to_sec()
            g.gaze_timestamp = RobotTimestamp(self.current_robot_gaze_timestamp)
            g.pan = angles[0]
            g.tilt = angles[1]
            self.current_robot_gaze = g
        self.lock.release()
        self.honor_stimulus_timeout()

    def point_callback(self, ros_data):
        self.lock.acquire()
        send_time = ros_data.header.stamp
        self.point_x = ros_data.point.x
        self.point_y = ros_data.point.y
        self.point_z = ros_data.point.z
        point = [self.point_x, self.point_y]
        # Derive coordinate mapping
        angles = self.trans.derive_mapping_coords(point)
        if angles is not None:
            g = RobotGaze()
            if self.mode == 'absolute':
                g.gaze_type = RobotGaze.GAZETARGET_ABSOLUTE
            else:
                g.gaze_type = RobotGaze.GAZETARGET_RELATIVE
            self.current_robot_gaze_timestamp = send_time.to_sec()
            g.gaze_timestamp = RobotTimestamp(self.current_robot_gaze_timestamp)
            g.pan = angles[0]
            g.tilt = angles[1]
            self.current_robot_gaze = g
        self.lock.release()
        self.honor_stimulus_timeout()

    def honor_stimulus_timeout(self):
        time.sleep(self.stimulus_timeout)

    def run(self):
        print ">>> Initializing ROS Subscriber to: %s" % self.inscope.strip()
        try:
            if self.datatype == "people":
                person_subscriber = rospy.Subscriber(self.inscope, People, self.people_callback, queue_size=1)
            elif self.datatype == "pointstamped":
                person_subscriber = rospy.Subscriber(self.inscope, PointStamped, self.point_callback, queue_size=1)
            else:
                print ">>> ROS Subscriber DataType not supported %s" % self.datatype.strip()
                return
        except Exception, e:
            print ">>> ERROR %s" % str(e)
            return
        self.ready = True
        while self.run_toggle is True:
            time.sleep(0.05)
        person_subscriber.unregister()
        print ">>> Deactivating ROS Subscriber to: %s" % self.inscope.strip()