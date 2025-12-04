# [ServerWorker.py]

from random import randint
import sys, traceback, threading, socket

from VideoStream import VideoStream
from RtpPacket import RtpPacket

import time

# ĐỊNH NGHĨA KÍCH THƯỚC PHÂN MẢNH
MAX_PAYLOAD_SIZE = 1400  # Giữ cho gói UDP < 1500 (MTU)
# Header JPEG: 4 byte (Offset) + 4 byte (Total Size)
JPEG_HEADER_SIZE = 8     

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		# Get the request type
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		# Get the media file name
		filename = line1[1]
		
		# Get the RTSP sequence number 
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				# Update state
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				# Generate a randomized RTSP session ID
				self.clientInfo['session'] = randint(100000, 999999)
				
				# Send RTSP reply
				self.replyRtsp(self.OK_200, seq[1])
				
				# Get the RTP/UDP port from the last line
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				
				self.replyRtsp(self.OK_200, seq[1])
				
				# Create a new thread and start sending RTP packets
				self.clientInfo['event'] = threading.Event()
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp) 
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				
				self.clientInfo['event'].set()
			
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")

			self.clientInfo['event'].set()
			
			self.replyRtsp(self.OK_200, seq[1])
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			
	def sendRtp(self):
		# Cấu hình FPS mục tiêu
		FPS = 24
		FRAME_PERIOD = 1.0 / FPS  # ~0.041s
		
		# Tính toán kích thước phân mảnh
		MAX_FRAGMENT_SIZE = MAX_PAYLOAD_SIZE - JPEG_HEADER_SIZE

		# Thời điểm bắt đầu chuẩn
		next_frame_time = time.time()

		while True:
			# Tính toán thời gian cần ngủ
			now = time.time()
			time_to_sleep = next_frame_time - now
			
			if time_to_sleep > 0:
				self.clientInfo['event'].wait(time_to_sleep)
			else:
				# Nếu bị trễ (xử lý quá lâu), không ngủ mà chạy ngay để đuổi kịp
				pass

			# Stop sending if PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet(): 
				break
				
			data = self.clientInfo['videoStream'].nextFrame()
			if data: 
				frameNumber = self.clientInfo['videoStream'].frameNbr()
    
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])

					# BẮT ĐẦU LOGIC PHÂN MẢNH
					total_size = len(data)
					offset = 0
     
					# Chuyển đổi total_size sang bytes (4 bytes)
					total_size_bytes = total_size.to_bytes(4, byteorder='big')

					while offset < total_size:
						# Lấy một mảnh dữ liệu
						chunk_size = min(total_size - offset, MAX_FRAGMENT_SIZE)
						fragment_data = data[offset : offset + chunk_size]

						# Tạo JPEG header (8-byte)
						offset_bytes = offset.to_bytes(4, byteorder='big')
						jpeg_header = offset_bytes + total_size_bytes

						# Payload của RTP = JPEG Header (8 bytes) + Dữ liệu mảnh
						payload = jpeg_header + fragment_data

						# Cập nhật offset
						offset += chunk_size

						# Đặt Marker bit: M=1 cho mảnh cuối cùng, M=0 cho các mảnh khác
						marker = 1 if offset == total_size else 0

						# Tạo và gửi gói RTP
						rtp_packet = self.makeRtp(payload, frameNumber, marker)
						self.clientInfo['rtpSocket'].sendto(rtp_packet, (address, port))
				except:
					print("Connection Error")
     
			# Cập nhật thời điểm cho frame KẾ TIẾP
			next_frame_time += FRAME_PERIOD

	def makeRtp(self, payload, frameNbr, marker): # <-- SỬA ĐỔI: Thêm `marker`
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		pt = 26 # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			#print("200 OK")
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		# Error messages
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")