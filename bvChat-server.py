from socket import *
import threading
import json
from pathlib import Path
import queue
import time

# store username and passwords as JSON in pathlib object
credentialFile = Path(__file__).parent / ".credentials.json"

# create the credential file if it doesn't exist.
if not credentialFile.exists():
    with open(str(credentialFile), "w") as f:
        f.write("{}")

port = 42424 # default server port

# contains all user credentials, where key is the username, a
#   and value is the user's password
userCredentials = {}

# contains booleans for each username representing whether the user is online or not.
userIsOnline = {}

# contains all incoming messages to each user
# each username is associated with a queue object that get processed when filled
userMessageBuffers = {}

# contains the number of login failures per user
auth_failures = {}

# contains the number of 30 second intervals a user needs 
#   for a user to be able to log in again
reauth_cooldown = {}

# timeout time retries x 30 seconds for a user to retry logging
#   in again after 3 unsuccessful login attempts in 30 seconds
CLIENT_AUTH_TIMEOUT = 4

# number of seconds the server waits before decrementing lockout timer
TIMEOUT_CHECKER_TIMEOUT = 30

# loop condiiton that controls when the main thread exits
running = True

MOTD = "We've been trying to reach you concerning your vehicle's extended warranty."

# mutexes #
statusLock = threading.Lock() # lock for accessing login state (logged in)
msgAccessLock = threading.Lock() # lock for accessing message queues 
authFailLock = threading.Lock() # lock for accessing auth_failures dict

# init a listener for clients to connect to
listener = socket(AF_INET, SOCK_STREAM)
listener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
listener.bind(('', port))
listener.listen(20) # allow 20 simultaneous connections

# this function loads user credentials as JSON from a credential file stored on disk
# this method is called when the server starts.
def loadCredentials():
    global userCredentials, userIsOnline
    with open(str(credentialFile), "r") as jsonFile:
        userCredentials = json.load(jsonFile)

    # for each user in the credential file, initialize internal data 
    #   structures for that user
    for username in userCredentials:
        initUser(username)

# write user credentials dict to credential file        
def writeCredentials():
    with open(str(credentialFile), "w") as jsonFile:
        json.dump(userCredentials, jsonFile)

# Fully fetches a message of `msgLength' bytes from the specified socket `conn'
def getFullMsg(conn, msgLength):
    msg = b''
    while len(msg) < msgLength:
        retVal = conn.recv(msgLength - len(msg))
        msg += retVal
        if len(retVal) == 0:
            break    
    return msg.decode()

# gets a message from a specified socket 'conn' until a newline is read.
#    the socket does not block to receive a message. Thus, if a message
#    is not received, the socket will time out and will return 
def getLineAsync(conn):
    msg = b''
    while True:
        try:
            ch = conn.recv(1, MSG_DONTWAIT)
            if ch == b'\n' or len(ch) == 0:
                break
            msg += ch
        except BlockingIOError:
            break
    return msg.decode()

# gets a message from a specified socket 'conn' until a newline is read.
#   the socket blocks until a message is received. 
def getLineSync(conn):
    msg = b''
    while True:
        ch = conn.recv(1)
        if ch == b'\n':
            break
        msg += ch
    return msg.decode()

# gets a list of all users that are currently connected to the server
def getOnlineUsers():
    users = []
    for user in userIsOnline:
        if userIsOnline[user]:
            users.append(user)
    return users

# initialize auth failure data structures for an ip address
def initAuthDetect(ip):
    authFailLock.acquire()
    auth_failures[ip] = 0
    reauth_cooldown[ip] = 0
    authFailLock.release()

# initialize all basic data structures for user data
# this method is called when the server starts.
def initUser(username):
    global userMessageBuffer
    msgAccessLock.acquire()
    userMessageBuffer = queue.Queue() # init the messages queue 
    userMessageBuffers[username] = userMessageBuffer # store it
    msgAccessLock.release()

    statusLock.acquire()
    userIsOnline[username] = False # by default, all users are offline
    statusLock.release()

# given a message and a username, send a message to every other
#   user that is connected to the server
def broadcastMessage(message, curr_user, server=False):
    global userMessageBuffers
    msgAccessLock.acquire()

    # iterate through each user in messagebuffers.
    for uname in userMessageBuffers:
        # don't send message to yourself or to users that are offline
        if uname == curr_user or userIsOnline[uname] == False:
            continue

        # when a message is stored, it is saved as a tuple containing
        #    the sender and the message
        #    if the server flag is set by the function caller, the sender
        #    is set to the server itself
        if server:
            userMessageBuffers[uname].put(("SERVER", message))
        else:
            userMessageBuffers[uname].put((curr_user, message))
    msgAccessLock.release()

# given a message, a recipient, and a sender, send one user a message
def unicastMessage(message, receiver, sender):
    global userMessageBuffers
    msgAccessLock.acquire()

    # use minecraft syntax for direct message and denote the sender
    unicastMessage = "UNICAST: " + sender + " whispers to you: " + message

    # send the messge to the recipient
    userMessageBuffers[receiver].put(("Server", unicastMessage))
    msgAccessLock.release()

# Process a given message from bvClient and respond appropriately with a socket connection
def handleCommand(msg, conn, curr_user, async_conn):
    if msg == "CLOSE":
        # close the socket connection
        cleanup(curr_user, conn, async_conn)
    # send the MOTD
    elif msg.startswith("MOTD"):
        sendSize = str(len(MOTD)) + "\n"
        conn.send(sendSize.encode())
        conn.send(MOTD.encode())
    # the client is sending a direct message
    elif msg.startswith("MSG_TELL:"):
        length = int(msg[9:]) # length of the incoming message
        msg = getFullMsg(conn, length) # get the full message
        colon = msg.find(":") # index of the colon that splits the username length from teh length of the message
        username_len = int(msg[:colon])
        receiver = msg[colon+1:colon+1+username_len] # the username of the message recipient
        msg_slice = msg[colon+1+username_len:] # the actual message

        # if the user doesn't exist, tell the user
        if not receiver in userIsOnline:
            conn.send(b"ERR_NOUSER\n")
            return 
        else:
            conn.send(b"ACK\n")
        # send the message
        unicastMessage(msg_slice, receiver, curr_user)

    # the client is sending a message to all online users
    elif msg.startswith("MSG_BROADCAST:"):
        length = int(msg[14:]) # length of the message in the header
        msg = getFullMsg(conn, length) # get the full message
        broadcastMessage(msg, curr_user) # broadcast it

    # the client is requesting a list of all online users
    elif msg.startswith("QUERY_ONLINE_USERS"):
        users_str = ", ".join(getOnlineUsers()) # make a comma-delimited string of users
        header = "USERS:"+ str(len(users_str)) + "\n" # get the length of user string
        conn.send(header.encode()) # SEND IT
        conn.send(users_str.encode())

    # send an emote message
    elif msg.startswith("/me: "):
        colon = msg.find(":", 5, -1) # find the colon seperating the username and length of the message
        msg_len = int(msg[5:colon])
        emote_txt = getFullMsg(conn, msg_len) # get the full message
        username = msg[colon+1:]
        broadcastMessage(emote_txt, username) # send the message

# given a username, get messages from their message buffer from when they were offline
def getOfflineMessages(username, conn):
    notificationMSG = "You have new direct messages: "
    msgAccessLock.acquire()
    size = userMessageBuffers[username].qsize() # get the number of messages in the queue
    if size > 0:
        # encode the length of the notification message and denote it as from the server
        header = str(len(notificationMSG)) + ":Server" + "\n"
        conn.send(header.encode()) # S E N D   I T
        conn.send(notificationMSG.encode())

        # keep looping until the queue is empty
        while userMessageBuffers[username].qsize() != 0:
            data = userMessageBuffers[username].get() # dequeue a tuple
            sender = data[0] # get the sender
            msg = data[1] # get the message
            unicast_header = str(len(msg)) + ":" + sender + "\n" # endocde the message length and sender
            conn.send(unicast_header.encode()) # s e n d
            conn.send(msg.encode())

    msgAccessLock.release()

# handle all the stuff that needs to happen before closing here
def cleanup(*args):
    username = args[0]
    # announce that elvis has left the building
    broadcastMessage(username + " has left the chat room.", username, True)
    statusLock.acquire()
    userIsOnline[username] = False # set online status to false
    statusLock.release()

    # depending on length of args, we close one or two sockets
    arg_len = len(args)
    if arg_len == 3:
        conn1 = args[1]
        conn2 = args[2]
        conn1.close()
        conn2.close()
    else:
        conn = args[1]
        conn.close()
    exit(0)

# thread for handling authentication failures
def authTimeoutManager():
    global auth_failures, reauth_cooldown
    while running:
        time.sleep(TIMEOUT_CHECKER_TIMEOUT) # wait 30 seconds
        authFailLock.acquire()
        for ip in auth_failures:
            if auth_failures[ip] > 0: # reset login attempts
                auth_failures[ip] = 0
            if reauth_cooldown[ip] > 0: # decrement timer for account lockout
                reauth_cooldown[ip] -= 1
        authFailLock.release()

# thread for handling each new connection
def handleClient(connInfo):
    global userCredentials, userIsOnline, userMessageBuffers, auth_failures, reauth_cooldown 
    
    # new connection objects
    clientConn, clientAddr = connInfo
    clientIP = clientAddr[0]
    print(f"New connection from {clientIP}!")
   
    done = False # loop variable, set to true when it is time to exit the thread
    auth = False # boolean for when the user successfully authenticates

    # boolean for calling cleanup method
    doCleanup = True

    # get initialization message from client
    init = getLineSync(clientConn)

    if clientIP in reauth_cooldown:

        # check to see if the client should be locked out 
        if reauth_cooldown[clientIP] > 0:
            # tell the client they are locked out and exit
            clientConn.send(b"ERR_AUTH_LOCKOUT\n")
            clientConn.close()
            exit()
        else:
            # else, send an acknowledgement
            clientConn.send(b"ACK\n")
    else:
        clientConn.send(b"ACK\n")

    # get the client's username, password, and a port to send async messages to
    username = getLineSync(clientConn)
    password = getLineSync(clientConn)
    msgSendPort = getLineSync(clientConn)

    # if connecting from a new IP, log it
    if clientIP not in auth_failures:
        initAuthDetect(clientIP)

    # authenticate user
    if username in userCredentials:
        if password == userCredentials[username]: # do the passwords match?
            if userIsOnline[username] == True: # is the user already connected?
                clientConn.send(b"ERR_CONCURRENT_CONNECTION\n") # no concurrent clients
                done = True # exit the loop below
            else:
                # tell the client authentication was successful
                clientConn.send(b"AUTH_GOOD\n")
                auth = True # auth true
        # auth failure
        else:
            authFailLock.acquire()
            auth_failures[clientIP] += 1 # log an authentication failure
            
            # check to see if the user failed to auth 3 times in 30 seconds
            if auth_failures[clientIP] >= 3: 
                reauth_cooldown[clientIP] = CLIENT_AUTH_TIMEOUT # lock them out if so
                auth_failures[clientIP] = 0 # reset auth failures
                clientConn.send(b"AUTH_LOCKOUT\n") # let the user know
            else:
                clientConn.send(b"AUTH_FAIL\n") # tell the client about failed auth
            authFailLock.release()
            done = True
    else:
        # first time connection
        userCredentials[username] = password # save password
        clientConn.send(b"AUTH_GOOD\n")
        initUser(username) # initialize data structures
        auth = True
        
    # if authentication was successful
    if auth:
        statusLock.acquire()
        userIsOnline[username] = True # change online status 
        statusLock.release()
        
        # initialize socket to send asynchronous messages to
        cmSocket = socket(AF_INET, SOCK_STREAM)
        cmSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        while True:
            try:
                # keep trying to connect until the client accepts
                cmSocket.connect((clientIP, int(msgSendPort)))
                break
            except ConnectionRefusedError:
                continue

        # tell all online users about our presence
        broadcastMessage(username + " has joined the chat room!", username, True)

        # get offline direct messages
        getOfflineMessages(username, cmSocket)
    
    try:
        while not done:
            msg = getLineAsync(clientConn) # check for control messages from client
            if msg != "":
                handleCommand(msg, clientConn, username, cmSocket) # process the message
            
            # check to see if there are any new messages to broadcast
            if userMessageBuffers[username].qsize() != 0:                
                msgAccessLock.acquire()
                data = userMessageBuffers[username].get() # get tuple
                sender = data[0] # sender
                msg = data[1] # message
                msgAccessLock.release()
                msgSize = str(len(msg)) + ":" + sender + "\n" # encode the message length and its sender
                cmSocket.send(msgSize.encode()) # send data via async socket:
                cmSocket.send(msg.encode())

            # sleep so the loop doesn't happen every CPU cycle
            time.sleep(.1)

        if doCleanup:
            if auth:
                cleanup(username, clientConn, cmSocket)
            else:
                cleanup(username, clientConn)

    # if a client suddenly disconnects, gracefully exit
    except BrokenPipeError:
        if doCleanup:
            if auth:
                cleanup(username, clientConn, cmSocket)
            else:
                cleanup(username, clientConn)

# initialize the authentication checker timeout
threading.Thread(target=authTimeoutManager, args=(), daemon=True).start()

# program start
while running: 
    try:
        # create a new thread for each connecting client
        threading.Thread(target=handleClient, args=(listener.accept(),), daemon=True).start()
    # if an exception occurs, exit gracefully
    except KeyboardInterrupt:
        print("Shutting down...")
        writeCredentials() # save user credentials to a file
        running = False
