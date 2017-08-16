import numpy as np
from scipy.optimize import minimize


""" some helper functions """

def fillNans(im2):
    valid_mask = ~np.isnan(im2)
    coords = np.arange(len(im2))[valid_mask]
    values = im2[valid_mask]

    return np.interp(np.arange(len(im2)), coords, values)

def getSquare(x, y):
    p0 = [x-0.5, y-0.5, 0]
    p1 = [x+0.5, y-0.5, 0]
    p2 = [x+0.5, y+0.5, 0]
    p3 = [x-0.5, y+0.5, 0]
    return np.array([p0, p1, p2, p3]).T

def getRectangle(w, d):
    p0 = [-w, d-w, 0]
    p1 = [+w, d-w, 0]
    p2 = [+w, d+w, 0]
    p3 = [-w, d+w, 0]
    return np.array([p0, p1, p2, p3]).T

def calcTrapezSize(rect):
    p0, p1, p2, p3 = rect.T
    a = abs(p0[0] - p1[0])
    c = abs(p2[0] - p3[0])
    h = np.mean([abs(p0[1] - p3[1]), abs(p1[1] - p2[1])])
    return (a + c) / 2 * h

def calcQuadrilateralSize(rect):
    A, B, C, D = rect.T
    return 0.5 * abs((A[1]-C[1])*(D[0]-B[0]) + (B[1]-D[1])*(A[0]-C[0]))

class CameraTransform():
    """
    CameraTransform class to calculate the position of objects from an image in 3D based
    on camera intrinsic parameters and observer position
    """
    t = None
    R = None
    C = None

    R_earth = 6371e3

    cam_location = None
    cam_heading = None
    cam_heading_rotation_matrix = None

    height = None
    roll = None
    heading = 0
    tilt = None
    tan_tilt = None

    pos_x = 0
    pos_y = 0

    fixed_horizon = None

    estimated_height = 30
    estimated_tilt = 85
    estimated_heading = 0
    estimated_roll = 0

    def __init__(self, focal_length, sensor_size, image_size, observer_height=None, angel_to_horizon=None):
        """
        Init routine to setup calculation basics

        :param focal_length:    focal length of the camera in mm
        :param sensor_size:     sensor size in mm [width, height]
        :param image_size:      image size in mm [width, height]
        :param observer_height: observer elevation in m
        :param angel_to_horizon: angle between the z-axis and the horizon
        """

        # store and convert arguments
        self.f = focal_length * 1e-3 # in m
        self.sensor_width, self.sensor_height = np.array(sensor_size) * 1e-3 # in m
        self.fov_h_angle = 2*np.arctan(self.sensor_width/(2*self.f))
        self.fov_v_angle = 2*np.arctan(self.sensor_height/(2*self.f))
        self.im_width, self.im_height = image_size

        # normalize the focal length by the sensor width and the image_width
        self.f = self.f / self.sensor_width * self.im_width

        # compose the intrinsic camera matrix
        self.C1 = np.array([[self.f, 0, self.im_width / 2, 0], [0, self.f, self.im_height / 2, 0], [0, 0, 1, 0]])

        if observer_height is not None:
            self.height = observer_height
            self.tilt = angel_to_horizon
            self._initCameraMatrix()

    def _initCameraMatrix(self, height=None, tilt_angle=None, roll_angle=None):
        # convert the angle to radians
        if tilt_angle is None:
            tilt_angle = self.tilt
        else:
            self.tilt = tilt_angle
        if height is None:
            height = self.height
        else:
            self.height = height
        angle = tilt_angle * np.pi / 180
        if roll_angle is None:
            if self.roll:
                roll = self.roll * np.pi / 180
            else:
                roll = 0
        else:
            roll = roll_angle * np.pi / 180

        if self.heading:
            heading = self.heading * np.pi / 180
        else:
            heading = 0

        if self.tan_tilt:
            angle = np.pi/2-np.arctan(self.tan_tilt)
            self.tilt = angle*180/np.pi

        # get the translation matrix and rotate it
        self.t = np.array([[self.pos_x, self.pos_y, -height]]).T

        # construct the rotation matrices for tilt, roll and heading
        self.R_tilt = np.array([[1, 0, 0],
                                [0,  np.cos(angle), np.sin(angle)],
                                [0, -np.sin(angle), np.cos(angle)]])
        self.R_roll = np.array([[+np.cos(roll), np.sin(roll), 0],
                                [-np.sin(roll), np.cos(roll), 0],
                                [            0,            0, 1]])
        self.R_head = np.array([[+np.cos(heading), np.sin(heading), 0],
                                [-np.sin(heading), np.cos(heading), 0],
                                [0, 0, 1]])

        # rotate the translation around the tilt angle
        self.t = np.dot(self.R_tilt, self.t)

        # get the rotation-translation matrix with the rotation composed with the translation
        self.R = np.vstack((np.hstack((np.dot(np.dot(self.R_roll, self.R_tilt), self.R_head), self.t)), [0, 0, 0, 1]))

        # compose the camera matrix with the rotation-translation matrix
        self.C = np.dot(self.C1, self.R)

    def transWorldToCam(self, x):
        """
        Transform from 3D world coordinates to 2D camera coordinates.

        :param x: a point in world coordinates [x, y, z], or an array of world points in the shape of [3xN]
        :return: a list of projected points
        """

        # reshape input x array to two dimensions
        try:
            if len(x.shape) == 1:
                x = x[:, None]
        except AttributeError:
            x = np.array([x]).T
        # add a 1 as a 3rd dimension for the projective coordinates
        x = np.vstack((x, np.ones(x.shape[1])))

        # multiply it with the camera matrix
        X = np.dot(self.C, x)
        # rescale it so the lowest component is again 1
        X = X[:2] / X[2]
        return X

    def _transCamToWorldFixedDimension(self, x, fixed, dimension):
        # add a 1 as a 3rd dimension for the projective coordinates
        x = np.vstack((x, np.ones(x.shape[1])))

        # make a copy of the projection matrix
        P = self.C.copy()
        # create a reduced matrix, that collapses two columns. Namely the desired fixed dimension with the
        # projective dimension e.g. if z is fixed
        # ( a b c d )   ( x*s )   ( a b c*z+d )   ( x*s )
        # ( e f g h ) * ( y*s ) = ( e f g*z+h ) * ( y*s )
        # ( i j k l )   ( z*s )   ( i j k*z+h )   (  s  )
        #               (  s  )
        P[:, dimension] = P[:, dimension] * fixed + P[:, 3]
        P = P[:, :3]
        # this results then in a 3x3 matrix corresponding to a system of linear equations
        X = np.linalg.solve(P, x)

        # the new vector has then to be rescaled from projective coordinates to normal coordinates
        # scaling by the value of the projective dimension entry
        X = X / X[dimension]
        # and adding the fixed value
        # (as we had used the entry of the fixed dimension for the projective entry, we can use this entry and overwrite
        #  it with the desired fixed value)
        X[dimension] = fixed

        return X

    def transCamToWorld(self, x, X=None, Y=None, Z=None):
        """
        Transform from 2D camera coordinates to 3D world coordinates. One of the 3D values has to be fixed. 
        This can be specified by supplying one of the three X, Y, Z.
        
        :param x: a point in camera coordinates [x, y], or an array of camera points in the shape of [2xN]
        :param X: when given project the camera points to world coordinates with their X value set to this parameter. Can be a single value or a list.
        :param Y: when given project the camera points to world coordinates with their Y value set to this parameter. Can be a single value or a list.
        :param Z: when given project the camera points to world coordinates with their Z value set to this parameter. Can be a single value or a list.
        :return: a list of projected points
        """

        # test whether the input is good
        if (X is None) + (Y is None) + (Z is None) != 2:
            raise ValueError("Exactly one of X, Y, Z has to be given.")

        # process the input
        if X is not None:
            fixed = X
            dimension = 0
        elif Y is not None:
            fixed = Y
            dimension = 1
        else:
            fixed = Z
            dimension = 2

        # reshape input x array to two dimensions
        try:
            x = np.array([[m.x, m.y] for m in x]).T
        except AttributeError:
            pass
        try:
            if len(x.shape) == 1:
                x = x[:, None]
        except AttributeError:
            x = np.array([x]).T

        # if the fixed value is a list, we have to transform each coordinate separately
        if not isinstance(fixed, int) and not isinstance(fixed, float):
            return np.array(
                [self._transCamToWorldFixedDimension(x[:, i:i + 1], fixed=fixed[i], dimension=dimension) for i in
                 range(x.shape[1])])[:, :, 0].T
        # else transform everything in one go
        return self._transCamToWorldFixedDimension(x, fixed, dimension)

    def transCamToEarth(self, x, H=None, max_iter=100, max_distance=0.01):
        result = []
        for point in x:
            last_point = None
            next_z = H
            for i in range(max_iter):
                new_point = self._transCamToWorldFixedDimension(point[:, None], fixed=next_z, dimension=2)
                alpha = np.acos(new_point[1]/(R_earth+H))
                if last_point is not None and np.linalg.norm(new_point-last_point) < max_distance:
                    result.append([new_point[0], R_earth*alpha, H])
                    break
                next_z = -np.sin(alpha)*(R_earth+H)
                last_point = new_point
            else:
                result.append([new_point[0], R_earth * alpha, H])
        return result

    def transEarthToCam(self, x):
        return self.transWorldToCam(self.transEarthToWorld(x))

    def transWorldToEarth(self, x):
        x = x.copy()
        earth_center = np.array([0, 0, -R_earth])
        r_eff = np.linalg.norm(x - earth_center, axis=0)
        x[1] = np.acos(x[1] / r_eff) * r_eff
        x[2] = r_eff
        return x

    def transEarthToWorld(self, x):
        x = x.copy()
        radius = x[2]
        alpha = x[1] / radius
        x[1] = np.cos(alpha) * radius
        x[2] = -np.sin(alpha) * radius
        return x

    def transGPSToEarth(self, x):
        x = x.copy()
        # latitude, longitude, height
        diff = np.array(self.cam_location - x[:2])
        diff = np.dot(self.cam_heading_rotation_matrix, diff)
        x[:2] = diff*np.pi/180*R_earth
        return x

    def transGPSToCam(self, x):
        return self.transEarthToCam(self.transGPSToEarth(x))

    def transCamToGPS(self, x, H=0):
        return self.transEarthToGPS(self.transCamToEarth(x, H))

    def transEarthToGPS(self, x):
        x = x.copy()
        x[:2] = x[:2]*180/np.pi/R_earth
        x[:2] = self.cam_location - np.dot(np.linalg.inv(self.cam_heading_rotation_matrix), x[:2])
        return x

    def setCamHeading(self, angle):
        angle = angle*np.pi/180
        self.cam_heading = angle
        self.cam_heading_rotation_matrix = np.array([[np.cos(angle), np.sin(angle)],
                                                     [-np.sin(angle), np.cos(angle)]])

    def setCamGPS(self, lat, lng):
        self.cam_location = np.array([lat, lng])

    def generateLUT(self, undef_value=0):
        """
        Generate LUT to calculate area covered by one pixel in the image dependent on y position in the image

        :return: LUT, same length as image height
        """
        horizon = self.getImageHorizon()
        print("horizon", horizon[1])
        y_stop = max([0, int(horizon[1][1])])
        y_start = self.im_height
        print(y_start, y_stop)

        self.y_lookup = np.zeros(self.im_height) + undef_value

        x = self.im_width/2

        for y in range(y_stop, y_start):
            rect = getSquare(x, y)[:2, :]
            rect = self.transCamToWorld(rect, Z=0)
            A = calcQuadrilateralSize(rect)
            self.y_lookup[y] = A

        return self.y_lookup

    def fitCamParametersFromObjects(self, points_foot=None, points_head=None, lines=None, object_height=1, object_elevation=0):
        """
        Fit the camera parameters for given objects of equal heights. The foot positions are given in points_foot and the
        heads are given in points_head. As an alternative the positions can be given as ClickPoints line objects in lines.
        The height of each objects is given in object_height, and if the objects are not at sea level, an object_elevation
        can be given.
        
        :param points_foot: The pixel positions of the feet of the objects in the image. 
        :param points_head:  The pixel positions of the heads of the objects in the image. 
        :param lines: An alternative for the points_foot and points_head arguments, ClickPoints lines can be directly given.
        :param object_height: The height of the objects.
        :param object_elevation: The elevation of the feet ot the objects.
        :return: the fitted parameters.
        """
        if lines is not None:
            y1 = [np.max([l.y1, l.y2]) for l in lines]
            y2 = [np.min([l.y1, l.y2]) for l in lines]
            x = [np.mean([l.x1, l.x2]) for l in lines]
            points_foot = np.vstack((x, y1))
            points_head = np.vstack((x, y2))

        def cost():
            estimated_foot_3D = self.transCamToWorld(points_foot.copy(), Z=object_elevation)
            estimated_foot_3D[2, :] = object_elevation+object_height
            estimated_head = self.transWorldToCam(estimated_foot_3D)
            pixels = np.linalg.norm(points_head - estimated_head, axis=0)
            return np.mean(pixels ** 2)

        def cost2():
            estimated_foot_3D = self.transCamToWorld(points_foot.copy(), Z=0)
            estimated_head_3D = self.transCamToWorld(points_head.copy(), Y=estimated_foot_3D[1, :])
            heights = estimated_foot_3D[2, :] - estimated_head_3D[2, :]
            return np.std((heights - object_height) ** 2)

        return self._fit(cost)

    def _getAngleFromHorizonAndHeight(self, horizon=None, height=None):
        if horizon is None:
            horizon = self.fixed_horizon
        if height is None:
            height = self.height
        angle = np.arccos(height / np.sqrt(height ** 2 + 2 * height * self.R_earth))
        angle = angle + (horizon - self.im_height / 2) / self.im_height * self.fov_v_angle
        return angle * 180 / np.pi

    def fixRoll(self, roll):
        """
        Set the roll parameter of the camera to a given value and hold it there in subsequent fitting functions.
        
        :param roll: The roll of the camera in degress. 
        """
        self.roll = roll

    def fixHeight(self, height):
        """
        Set the height parameter of the camera to a given value and hold it there in subsequent fitting functions.

        :param height: The height of the camera in meters.
        """
        self.height = height

    def fixHorizon(self, horizon):
        """
        Fix the horizon to go through the points given. This will adjust in subsequent fitting functions the tilt angle
        to always match the horizon with these points. Also if no roll angle has been specified before, the roll angle is
        fitted from the horizon.
        
        :param horizon: Pixel coordinates of points at the horizon in the shape of [2xN] 
        """
        # if the horizon is given in ClickPoints markers, split them in x and y component
        try:
            horizon = np.array([[m.x, m.y] for m in horizon]).T
        except AttributeError:
            pass
        # fit a line through the points
        m, t = np.polyfit(horizon[0, :], horizon[1, :], deg=1)
        # calculate the center of the line
        self.fixed_horizon = self.im_width / 2 * m + t
        # set the roll if it is not fixed yet
        if self.roll is None:
            self.roll = -np.arctan(m)*180/np.pi
        # update the camera matrix if we already have a height
        if self.height is not None:
            self.tilt = self._getAngleFromHorizonAndHeight(self.im_width / 2 * m + t, self.height)
            self._initCameraMatrix()

    def fitCamParametersFromLandmarks(self, marks, distances, heading=None):
        """
        Fit the camera parameters form objects of known distance to the camera.
        
        :param marks: The pixel positions of the objects in the image. In the shape of [2xN] 
        :param distances: The distances of the mark points to the camera.
        :param heading: Optional a heading angle in degrees of the objects. When given the heading of the camera will be fitted, too.
        :return: the fitted parameters.
        """
        # if the horizon is given in ClickPoints markers, split them in x and y component
        try:
            marks = np.array([[m.x, m.y] for m in marks]).T
        except AttributeError:
            pass

        def cost():
            estimated_pos_3D = self.transCamToWorld(marks.copy(), Z=0)
            return np.mean((distances-estimated_pos_3D[1, :])**2)

        if heading is not None:
            self.heading = None
            marks_3D = []
            for dist, head in zip(distances, heading):
                marks_3D.append(np.array([np.sin(head*np.pi/180)*dist, np.cos(head*np.pi/180)*dist, 0]))
            marks_3D = np.array(marks_3D).T

            def cost():
                estimated_pos_3D = self.transCamToWorld(marks.copy(), Z=0)
                return np.mean(np.linalg.norm(estimated_pos_3D-marks_3D, axis=0)**2)

        return self._fit(cost)

    def fitCamParametersFromLengths(self, points, distances):
        """
        Fit the camera parameters form objects of known distance to the camera.

        :param points: TODO 
        :param distances: The distances of the mark points to the camera.
        :return: the fitted parameters.
        """
        # if the horizon is given in ClickPoints markers, split them in x and y component
        try:
            points1 = np.array([[m.x1, m.y1] for m in points]).T
            points2 = np.array([[m.x2, m.y2] for m in points]).T
        except AttributeError:
            points1, points2 = points

        def cost():
            p1 = self.transCamToWorld(points1, Z=0)
            p2 = self.transCamToWorld(points2, Z=0)
            calculated_dist = np.linalg.norm(p2-p1, axis=0)
            return np.mean((distances - calculated_dist) ** 2)

        return self._fit(cost)

    def _fit(self, cost):
        # define the fit parameters and their estimates
        estimates = {"height": self.estimated_height, "tan_tilt": np.tan((90-self.estimated_tilt)*np.pi/180), "roll": self.estimated_roll, "heading": self.estimated_heading}
        estimates = {"height": self.estimated_height, "tilt": self.estimated_tilt, "roll": self.estimated_roll, "heading": self.estimated_heading, "pos_x": 0, "pos_y": 0}
        fit_parameters = list(estimates.keys())

        # remove known parameters from list
        if self.roll is not None:
            fit_parameters.remove("roll")
        if self.heading is not None:
            fit_parameters.remove("heading")
        if self.height is not None:
            fit_parameters.remove('height')
        if self.pos_x is not None:
            fit_parameters.remove("pos_x")
        if self.pos_y is not None:
            fit_parameters.remove("pos_y")

        self.horizon_error = 0
        # define error function as a wrap around the cost function
        def error(p):
            # set the fit parameters
            for key, value in zip(fit_parameters, p):
                setattr(self, key, value)
            # calculate the camera matrix
            self._initCameraMatrix()

            if self.fixed_horizon:
                horizon = self.getImageHorizon()
                m, t = np.polyfit(horizon[0, :], horizon[1, :], deg=1)
                # calculate the center of the line
                fixed_horizon2 = self.im_width / 2 * m + t
                self.horizon_error = abs(self.fixed_horizon - fixed_horizon2)*0.01

            # calculate the cost function
            return cost()+self.horizon_error

        # minimize the unknown parameters with the given cost function
        p = minimize(error, [estimates[key] for key in fit_parameters])
        # call a last time the error function to ensure that the camera matrix has been set properly
        error(p["x"])
        # print the results and return them
        print({key: value for key, value in zip(fit_parameters, p["x"])})
        if "tan_tilt" in fit_parameters:
            print("tilt", self.tilt)
        return p

    def distanceToHorizon(self):
        return np.sqrt(2 * self.R_earth ** 2 * (1 - self.R_earth / (self.R_earth + self.height)))

    def getImageHorizon(self):
        """
        This function calculates the position of the horizon in the image sampled at the points x=0, x=im_width/2, x=im_width.
        
        :return: The points im camera image coordinates of the horizon in the format of [2xN]. 
        """
        # calculate the distance to the horizon and make a copy of the camera matrix
        distance = self.distanceToHorizon()
        P = self.C.copy()
        # compose a mixed transformation, where we fix 3D_Y to distance, 3D_Z to 0
        P[:, 1] = P[:, 1] * distance + P[:, 2] * 0 + P[:, 3]
        # and bring 2D_Y to the other side to search for it, too
        P[:, 2] = [0, -1, 0]
        # to the unknown values are 3D_X, 2D_Y and 3D_Scale
        P = P[:, :3]
        # this means vectors in the left side of the equation have the shape of [2D_X, 0, 1]
        x = np.array([[0, 0, 1], [self.im_width/2, 0, 1], [self.im_width, 0, 1]]).T
        # solve
        X = np.linalg.solve(P, x)
        # enter the found 2D_Y values into the vector
        x[1, :] = X[2, :]
        x = x[:2, :]
        # return the results
        return x

    def getImageExtend(self):
        points = np.array([[0, 0], [0, self.im_height], [self.im_width, self.im_height], [self.im_width, 0]]).T
        points = self.transCamToWorld(points, Z=0)
        return points

    def getTopViewOfImage(self, im, extent=None, scaling=None, doplot=False):
        """
        Transform the given image of the camera to a top view, e.g. project it on the 3D plane and display a birds view.
        
        :param im: The image of the camera. 
        :param extent: The part of the 3D plane to show: [xmin, xmax, ymin, ymax]. The same format as the extent parameter in in plt.imshow.
        :param scaling: How many pixels to use per meter. A smaller value gives a more detailed image, but takes more time to calculated.
        :param doplot: Whether to plot the image directly, with the according extent settings.
        :return: the transformed image
        """
        if extent is None:
            x, y, z = self.getImageExtend()
            extent = [min(-x), max(-x), min(y), max(y)]
            print("extent", extent)

        # split the extent
        xlim, ylim = extent[:2], extent[2:]
        width = xlim[1]-xlim[0]
        distance = ylim[1]-ylim[0]
        # if no scaling is given, scale so that the resulting image has an equal amount of pixels as the original image
        if scaling is None:
            scaling = (width*distance)/(self.im_width*self.im_height)*100
        # copy the camera matrix
        P = self.C.copy()
        # set scaling and offset
        f = scaling
        xoff = -xlim[1]
        yoff = ylim[0]
        # offset and scale the camera matrix
        P = np.dot(P, np.array([[f, 0, 0, xoff], [0, f, 0, yoff], [0, 0, f, 0], [0, 0, 0, 1]]))
        # transform the camera matrix so that it projects on the z=0 plane (for details see transCamToWorld)
        P[:, 2] = P[:, 2] * 0 + P[:, 3]
        P = P[:, :3]
        # invert the matrix
        P = np.linalg.inv(P)
        # transform the image using OpenCV
        import cv2
        im = cv2.warpPerspective(im, P, dsize=(int(width/f), int(distance/f)))[::-1, ::-1]
        # and plot the image if desired
        if doplot:
            from matplotlib import pyplot as plt
            plt.imshow(im, extent=[xlim[0], xlim[1], ylim[0], ylim[1]])
        # return the image
        return im
