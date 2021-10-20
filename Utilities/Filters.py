import numpy as np
import datetime
import matplotlib.pyplot as plt
from KalmanSmoother.Utilities.Utilities import dx_dy_distance
from geopy.distance import GreatCircleDistance


I = np.identity(4)
max_vel_uncert= 30
max_vel = 35
max_x_diff = 50

class FilterBase(object):
	drift_depth = -1200
	def __init__(self,float_class,sources,depth,stream,process_position_noise=5,process_vel_noise =1
		,depth_flag=False,stream_flag=False,lin_between_obs=False):
		self.depth_flag = depth_flag
		self.stream_flag = stream_flag
		self.lin_between_obs = lin_between_obs
		self.depth = depth
		self.stream = stream
		self.sources = sources
		self.float=float_class
		self.process_position_noise = process_position_noise
		self.process_vel_noise = process_vel_noise
		self.max_vel_uncert = max_vel_uncert
		self.Q = np.diag([self.process_position_noise,self.process_vel_noise,self.process_position_noise,self.process_vel_noise])
		self.Q_position_uncert_only = np.diag([self.process_position_noise,0,self.process_position_noise,0])
		self.date_list = []
		self.P_m = []
		self.P_p = [self.Q]
		self.X_p = [self.initialize_X()]
		self.X_m = []
		self.set_date(self.float.gps.date[0])
		self.variable_check = {'x_m-x_p':[],'innovation':[],'innovation label':[]}
		# assert self.float.gps.date[0]==self.date

	def set_date(self,date):
		self.date = date
		self.sources.set_date(date)
		self.float.clock.set_date(date)
		self.date_list.append(date)

	def increment_date(self):
		self.set_date(self.date+datetime.timedelta(days=1))

	def decrement_date(self):
		self.set_date(self.date-datetime.timedelta(days=1))

	def initialize_velocity(self):
#we initialize velocity with our best first guess so that 
#there isnt a shock to the system when the apriori is not in line with the observations
		date_diff = (self.float.gps.date[1]-self.float.gps.date[0]).days
		dy,dx = dx_dy_distance(self.float.gps.obs[1],self.float.gps.obs[0])
		lat_vel = dy/date_diff
		lon_vel = dx/date_diff
		return (lat_vel,lon_vel)

	def initialize_X(self):
		lat_vel,lon_vel = self.initialize_velocity()
		deploy_loc = self.float.gps.obs[0]
		X = np.array([0,lon_vel, \
		0,lat_vel]).reshape(4,1)
#state vector is defined as lon, lon speed, lat, lat speed
#this is recorded in KM and referenced back to the starting position
		return X

	def increment_filter(self):
		self.X_m.append(self.A.dot(self.X_p[-1]))# + B.dot(u[:,k])				#predicted state estimate
		self.P_m.append(self.P_increment())
		gps,toa,depth,stream,interp = self.float.return_data()

		depth_xm = []
		if (not depth)&(self.depth_flag)&(not gps):
			depth = [self.depth.return_z(self.pos_from_state(self.X_p[-1]))]
		if depth:
			depth_xm = [self.depth.return_z(self.pos_from_state(self.X_m[-1]))]
			k = -1 
			while np.isnan(depth_xm):
				k -= 1
				#print(k)
				depth_xm = self.depth.return_z(self.pos_from_state(self.X_p[k]))
		if depth:
			if depth[0]>self.drift_depth:
				depth[0] = self.drift_depth
			self.depth_noise_multiplier = 1.
			if depth[0]>-1200:
				self.depth_noise_multiplier = 2.
		stream_xm = []
		if (self.stream_flag)&(not gps):
			stream = [self.stream.return_z(self.pos_from_state(self.X_p[-1]))]
		if stream:
			stream_xm = [self.stream.return_z(self.pos_from_state(self.X_m[-1]))]
			k = -1 
			while np.isnan(stream_xm):
				k -= 1
				#print(k)
				stream_xm = self.stream.return_z(self.pos_from_state(self.X_p[k]))

		# else:
			# print 'no depth recorded'

		obs_num = len(gps)*2+len(toa)+len(depth)+len(stream)+len(interp)*2 #2 for lat lon, one for depth
		h = np.array(self.h_constructor(gps,toa,depth_xm,stream_xm,interp,self.pos_from_state(self.X_m[-1]))).reshape([obs_num,1])
		J = np.array(self.J_constructor(gps,toa,depth,stream,interp)).reshape([obs_num,4])
		Z,label = self.Z_constructor(gps,toa,depth,stream,interp)
		Z = np.array(Z).reshape([obs_num,1])
		R = np.array(self.R_constructor(gps,toa,depth,stream,interp)).reshape([obs_num,obs_num])
		Y = Z-h #innovation
		print('Y = ',Y)
		S = J.dot(self.P_m[-1].dot(J.T))+R 						#innovation covariance
		K = self.P_m[-1].dot(J.T.dot(np.linalg.inv(S)))
		self.X_p.append(self.X_checker(self.X_m[-1]+K.dot(Y)))
		self.P_p.append((I-K.dot(J)).dot(self.P_m[-1]))
		self.variable_check['x_m-x_p'].append((self.date,self.X_m[-1]-self.X_p[-1]))
		for dummy in zip(label,Y.tolist()):
			dummy[0].append(dummy[1])
		try:
			assert self.X_p[-1].shape == (4,1)
			assert self.X_m[-1].shape == (4,1)
			assert self.P_p[-1].shape == (4,4)
			assert self.P_m[-1].shape == (4,4)
			assert not np.isnan(self.X_p[-1]).any()
			assert not np.isnan(self.X_m[-1]).any()
			assert not np.isnan(self.P_p[-1]).any()
			assert not np.isnan(self.P_m[-1]).any()
		except AssertionError:
			raise

	def P_increment(self):
		if self.eig_checker(self.P_p[-1][[1,1,3,3],[1,3,1,3]],max_vel_uncert):
			Q = self.Q			#predicted estimate covariance
		else:
			Q = self.Q_position_uncert_only
		return self.A.dot(self.P_p[-1].dot(self.A.T))+Q

	def eig_checker(self,C,value):
		# print C
		C = C.reshape([2,2])
		# print C
		w,v = np.linalg.eig(C)
		return 2*max(w)*np.sqrt(5.991)<value

	def X_checker(self,X):
		for idx in [1,3]:
			dummy = X[idx]
			if abs(dummy)>max_vel:
				X[idx] = np.sign(dummy)*max_vel
		for idx in [0,2]:
			dummy_1 = X[idx]
			dummy_2 = self.X_m[-1][idx]
			diff = dummy_1-dummy_2
			if abs(dummy_1-dummy_2)>max_x_diff:
				X[idx] = dummy_2 + np.sign(diff)*max_x_diff
		return X

	def pos_from_state(self,state):
		pos = self.float.gps.obs[0].add_displacement(state.flatten()[0],state.flatten()[2])
		if np.isnan(pos.latitude):
			pos = self.float.gps.obs[0]
		return pos

	def toa_detrend(self,toa,sound_source):
		toa = self.float.clock.detrend_offset(toa)
		toa = sound_source.clock.detrend_offset(toa)
		return toa

	def h_constructor(self,gps,toa,depth,stream,interp,pos):
		h = []
		if gps:
			print('this is h gps')
			dy,dx = dx_dy_distance(pos,self.float.gps.obs[0])
			h.append(dx)
			h.append(dy)
		if interp:
			print('this is h interp')
			dy,dx = dx_dy_distance(pos,self.float.gps.obs[0])
			h.append(dx)
			h.append(dy)
		for (toa_reading,sound_source) in toa:
			dist = GreatCircleDistance(sound_source.position,pos).km
			h.append(sound_source.toa_from_dist(dist))
		if depth:
			h.append(depth[0])
		if stream:
			h.append(stream[0])
		return h

	def J_constructor(self,gps,toa,depth,stream,interp):
		J = []
		if gps:
			J.append([1,0,0,0])
			J.append([0,0,1,0])
		if interp:
			J.append([1,0,0,0])
			J.append([0,0,1,0])
		for (toa_reading,sound_source) in toa:
			state_pos = self.pos_from_state(self.X_m[-1])
			print('this is the J toa source position')
			dy,dx = dx_dy_distance(state_pos,sound_source.position)
			dist = GreatCircleDistance(state_pos,sound_source.position).km
			dT_dx = dx/dist*sound_source.slow()
			dT_dy = dy/dist*sound_source.slow()
			J.append([dT_dx,0,dT_dy,0])
		if depth:
			k = -1 
			dz_dx,dz_dy = self.depth.return_gradient(self.pos_from_state(self.X_p[k]))
			while any([np.isnan(dz_dx),np.isnan(dz_dy)]):
				k -= 1
				dz_dx,dz_dy = self.depth.return_gradient(self.pos_from_state(self.X_p[k]))
			J.append([dz_dx,0,dz_dy,0]) #now add depth jacobian
		if stream:
			k = -1 
			dz_dx,dz_dy = self.stream.return_gradient(self.pos_from_state(self.X_p[k]))
			while any([np.isnan(dz_dx),np.isnan(dz_dy)]):
				k -= 1
				#print(k)
				dz_dx,dz_dy = self.stream.return_gradient(self.pos_from_state(self.X_p[k]))
			J.append([dz_dx,0,dz_dy,0]) #now add stream jacobian
		return J

	def Z_constructor(self,gps,toa,depth,stream,interp,error_label='innovation'):
		Z = []
		label = []
		for _ in gps:
			print('this is Z gps')
			dy,dx = dx_dy_distance(gps[0],self.float.gps.obs[0])
			Z.append(dx)
			label.append(self.float.gps.dx_error[error_label])
			Z.append(dy)
			label.append(self.float.gps.dy_error[error_label])
		for _ in interp:
			print('this is Z interp')
			dy,dx = dx_dy_distance(interp[0],self.float.gps.obs[0])
			Z.append(dx)
			label.append(self.float.gps.dx_interp_error[error_label])
			Z.append(dy)
			label.append(self.float.gps.dy_interp_error[error_label])
		for (toa_reading,sound_source) in toa:
			toa_actual = self.toa_detrend(toa_reading,sound_source)
			Z.append(toa_actual)
			label.append(sound_source.error[error_label])
		if depth:
			Z.append(depth[0])
			label.append(self.float.depth.error[error_label])
		if stream:
			Z.append(stream[0])
			label.append(self.float.stream.error[error_label])
		return (Z,label)

	def R_constructor(self,gps,toa,depth,stream,interp):
		R = []
		if gps:
			R.append(gps_noise)
			R.append(gps_noise)
		if interp:
			R.append(interp_noise)
			R.append(interp_noise)
		for _ in toa:
			R.append(toa_noise)
		if depth:
			R.append(depth_noise/self.depth_noise_multiplier)
		if stream:
			R.append(stream_noise)
		return np.diag(R)

	def error_calc(self,pos_list,error_label):
		innovation_list = []
		innovation_label_list = []
		self.set_date(self.float.gps.date[0])
		while self.date<=self.float.gps.date[-1]:
			assert self.date == self.float.clock.date
			gps,toa,depth,stream,interp = self.float.return_data()
			pos = self.float.return_pos()
			Z,label = self.Z_constructor(gps,toa,depth,stream,interp,error_label)
			h = self.h_constructor(gps,toa,depth,stream,interp,pos)
			Y = np.array(Z)-np.array(h) #innovation
			Y = Y.reshape(len(Z),1)
			for dummy in zip(label,Y.tolist()):
				dummy[0].append(dummy[1])
			self.increment_date()

	def obs_date_diff_list(self,date_list):
		dates = np.sort(self.float.toa.date+self.float.gps.date)
		date_diff_list = []
		for date in date_list:
			dates_holder = dates[dates<date]
			if list(dates_holder):
				diff = (date-max(dates_holder)).days
				date_diff_list.append(diff)
			else:
				date_diff_list.append(0)
		return date_diff_list

	def X_m_minus_X_p_diagnostic(self):
		date_list,innovation = zip(*self.variable_check['x_m-x_p'])
		date_diff_list = self.obs_date_diff_list(date_list)
		innovation = np.array([np.sqrt(_[0]**2+_[2]**2) for _ in innovation]).flatten()

		plt.scatter(range(len(innovation)),innovation,s=0.3,c=np.array(date_diff_list),cmap=plt.cm.get_cmap("winter"))
		plt.colorbar(label='days since position')
		plt.xlabel('time step')
		plt.ylabel('innovation (km)')
		plt.savefig(str(self.float.floatname)+'-innovation-date-diagnostic')
		plt.close()

	def diagnostic_plot(self,innovation_list,label_list,label):
		flat_label_list = [item for sublist in label_list for item in sublist]
		flat_innovation_list = [item for sublist in innovation_list for item in sublist]
		for variable_label in np.unique(flat_label_list):
			dummy_list = []
			for _ in zip(label_list,innovation_list):
				try:
					idx = _[0].index(variable_label)
					flat_value_list = [item for sublist in _[1] for item in sublist]
					dummy_list.append(flat_value_list[idx])
				except ValueError:
					dummy_list.append(np.nan)
			plt.scatter(range(len(dummy_list)),dummy_list)
			plt.title(str(self.float.floatname)+' '+label+' '+variable_label)
			plt.savefig(str(self.float.floatname)+'_'+label+'_'+variable_label)
			plt.close()

	def innovation_diagnostic(self):
		self.diagnostic_plot(self.variable_check['innovation'],self.variable_check['innovation label'],'innovation')

	def linear_interp_between_obs(self):
		unique_date_list = np.sort(np.unique(self.date_list))
		date_diff_list = self.obs_date_diff_list(unique_date_list)
		idxs = np.where(np.array(date_diff_list)==14)[0]
		obs_dates = np.array(np.sort(self.float.toa.date+self.float.gps.date))
		unique_date_list = np.array(unique_date_list)
		for idx in idxs:
			date = unique_date_list[idx]
			min_date = max(obs_dates[obs_dates<date])-datetime.timedelta(days=4)
			max_date = min(obs_dates[obs_dates>date])+datetime.timedelta(days=4)
			min_idx = self.date_list.index(min_date)
			max_idx = self.date_list.index(max_date)

			min_pos = self.float.pos[min_idx]
			max_pos = self.float.pos[max_idx]

			time_delta = (max_date-min_date).days
			pos_insert = [min_pos + (_+1)*(max_pos-min_pos)/(time_delta+2) for _ in range(time_delta)]
			self.float.pos[min_idx:max_idx] = pos_insert


class LeastSquares(FilterBase):
	def __init__(self,float_class,sources,depth,stream,**kwds):
		super(LeastSquares,self).__init__(float_class,sources,depth,stream,**kwds)
		self.A=np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]) 
		self.increment()
# state matrix propogates to future with X(t)=X(t-1), V(t)=V(t-1)
	def increment(self):
		date_list = []
		date_list.append(self.date)
		while self.date<=self.float.gps.date[-1]:
			#print(self.date)
			self.increment_date()
			date_list.append(self.date)
			assert self.date == self.float.clock.date
			self.increment_filter()
		self.float.ls_pos = [self.pos_from_state(_) for _ in self.X_p]
		assert len(self.float.ls_pos)==len(date_list)
		# self.diagnostic_plot(error_list,label_list,'kalman_error')


class Kalman(FilterBase):
	def __init__(self,float_class,sources,depth,stream,**kwds):
		super(Kalman,self).__init__(float_class,sources,depth,stream,**kwds)
		self.A=np.array([[1,1,0,0],[0,0.95,0,0],[0,0,1,1],[0,0,0,0.95]]) 
		self.increment()
# state matrix propogates to future with X(t)=X(t-1)+V(t-1), V(t)=V(t-1)
	def increment(self):
		date_list = []
		date_list.append(self.date)
		while self.date<=self.float.gps.date[-1]:
			#print(self.date)
			self.increment_date()
			print(self.date)
			date_list.append(self.date)
			assert self.date == self.float.clock.date
			self.increment_filter()
		self.float.pos = [self.pos_from_state(_) for _ in self.X_p]
		if self.lin_between_obs:
			self.linear_interp_between_obs()
		assert len(self.float.pos)==len(date_list)
		self.float.pos_date = date_list
		self.error_calc(self.float.pos,'kalman')
		self.float.kalman_pos = self.float.pos
		# self.diagnostic_plot(error_list,label_list,'kalman_error')

	def state_vector_to_pos(self):		
		vel = []
		pos = []
		for _ in self.X_p:
			x = _[0]
			y = _[2]
			if (x==0) and (y==0):
				pos.append(self.float.gps.obs[0])
				vel.append((0,0))
			else:
				pos.append(self.float.gps.obs[0].add_displacement(x,y))
				vel.append((_[1],_[3]))
		return (pos,vel)


	def plot_position(self):
		plt.figure()
		plt.subplot(2,1,1)
		pos,vel = self.state_vector_to_pos()
		x,y = zip(*[(_.longitude,_.latitude)for _ in pos])
		plt.plot(x,y)
		plt.subplot(2,1,2)
		x_vel,y_vel = zip(*vel)
		plt.plot(x_vel)
		plt.plot(y_vel)
		plt.show()

class Smoother(Kalman):
	def __init__(self,float_class,sources,depth,stream,**kwds):
		super(Smoother,self).__init__(float_class,sources,depth,stream,**kwds)
		self.X = []
		self.P = []
		self.X.append(self.X_p.pop())
		self.P.append(self.P_p.pop())
		self.decrement_date()
		while self.date>=self.float.gps.date[0]:
			self.decrement_filter()
			self.decrement_date()
		self.float.pos = [self.pos_from_state(_) for _ in self.X[::-1]]
		if self.lin_between_obs:
			#print("linear interp between obs")
			self.linear_interp_between_obs()
		self.error_calc(self.float.pos,'smoother')
		self.float.P = self.P

	def decrement_filter(self):
		K = self.P_p[-1].dot(self.A.T.dot(np.linalg.inv(self.P_m[-1])))
		self.P.append(self.P_p.pop() - K.dot((self.P_m.pop()-self.P[-1]).dot(K.T)))
		self.X.append(self.X_p.pop() + K.dot(self.X[-1]-self.X_m.pop()))