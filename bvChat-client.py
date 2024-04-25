from socket import *
from sys import argv
import time
import curses
import threading
import traceback

# when client starts:
# bvChat-client.py IP_address port
# username & password

# need 3 arguments: program name, the IP address of the server, and the server port
if len(argv) < 3:
    print("usage: bvChat-Client.py <IP_ADDRESS> <PORT>")
    exit()

ip = argv[1]
port = int(argv[2])

# displays all available commands
HELP_STR = '''/motd: displays message of the day
/exit: leaves the chatroom
/tell: sends a direct message to another user, regardless of online status 
    Format: /tell <username> <message>
/me: displays an emote message
/who: displays a list of all the users that are currently online'''
HELP_STR_LEN = len(HELP_STR)

# init the socket for communicating with the server
clientSocket = socket(AF_INET, SOCK_STREAM)
clientSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)

# array for holding all messages being rendered by curses
allMessages = []

# list containing new message to be moved to allMessages. This is so curses doesn't
#   have to re-render messages until a new one arrives
newMessages = []

# lock for accessing allMessages and newMessages
messageLock = threading.Lock()

# socket for receiving asynchronous messages from the server
rcvListener = socket(AF_INET, SOCK_STREAM)
rcvListener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
rcvListener.bind(("", 0))
listenerPort = rcvListener.getsockname()[1] # let OS choose port

# height and width of curses window
width = 76
height = 18

# loop variable that controls the exiting of async message receive thread
rcvDone = False

# fully fetches a message of msgLength bytes from the specified socket 'conn'
def getFullMsg(conn, msgLength):
    msg = b''
    while len(msg) < msgLength:
        retVal = conn.recv(msgLength - len(msg))
        msg += retVal
        if len(retVal) == 0:
            break    
    return msg.decode()

# gets a message form a specified socket 'conn' until a newline is read.
#   the socket does not block to receive a message. Thus, if a message 
#   is not received, the socket will time out and will return 
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
    if len(msg) == 0:
        return "0"

    return msg.decode()

# gets a message from a specified socket 'conn' until a newline is read.
#   the socket blocks until a message is received
def getLineSync(conn):
    msg = b''
    while True:
        try:
            ch = conn.recv(1)
            if ch == b'\n':
                break
            msg += ch
        except TimeoutError:
            break
    return msg.decode()

# show the user an error message specified by err
def showError(err):
    messageLock.acquire()
    newMessages.append(("/ERR: " + err,0))
    messageLock.release()

# process text typed by the user
def handleCommand(text, conn, username):
    # socket message prefixes
    # prefixes are split between data with :
    # broadcast - 'MSG_BROADCAST:'
    # tell - 'MSG_TELL:'
    # me - 'MSG_BROADCAST_ME:'
    # who - 'QUERY_ONLINE_USERS:'
    # exit - 'CLOSE'
    # motd = 'MOTD'
    # if starts with a slash, the user typed a command
    if text.startswith("/"):
        # the user typed a direct message
        if text.startswith("/tell"):
            badCommandMsg = "Invalid Command. Type '/help' to see a list of commands."
            space_init = text.find(" ") # space seperating command name from recipient
            if space_init == -1:
                showError(baCommandMsg)
                return
            space_username = text.find(" ", space_init+1, -1) # space between recipient and the message being sent
            if space_username == -1:
                showError(badCommandMsg)
                return
            username = text[space_init+1:space_username] # username of recipient
            message = text[space_username+1:]
            user_len = len(username)
            control_send = str(user_len) + ":" + username + message # message body
            msg_len = "MSG_TELL:" + str(len(control_send)) + "\n" # header info
            conn.send(msg_len.encode())
            conn.send(control_send.encode())

            # process response from server
            resp = getLineSync(conn)

            # if the recipient doesn't exist, tell the user
            if resp == "ERR_NOUSER":
                showError("[Server]: The user [%s] does not exist." % username)

        # get a list of all online users
        elif text.startswith("/who"):
            control_send = "QUERY_ONLINE_USERS\n"
            conn.send(control_send.encode())
        
            users_header = getLineSync(conn)
            users_len = int(users_header[6:])
            users_str = getFullMsg(conn, users_len) # the comma delimited list of usernames
            formatOnlineUsers(users_str) # format and display online users

        # display emote message
        elif text.startswith("/me "):
            me_header = "/me: " + str(len(text)) + ":" + username + "\n" # encode message length and sender
            conn.send(me_header.encode())
            conn.send(text.encode())
            handleMessage(text, username) # display the message on the screen

        # get the MOTD from the server
        elif text.startswith("/motd"):
            conn.send(b"MOTD\n")
            motd_size = int(getLineSync(conn)) # get the length of the MOTD message
            motd = getFullMsg(conn, motd_size) # the full msg
            motd_msg = "/MOTD: " + motd
            handleMessage(motd_msg, username) # display the MOTD
        elif text.startswith("/help"):
            handleMessage(text, "Server") # display all available commands
        elif text.startswith("/exit"):
            cleanup(conn) # exit gracefully
        else:
            # a slash without the above was found, thus it is an invalid command
            showError("Invalid command. Type '/help' to see a list of commands.")

    # if no slash, the user typed a message to send to all other clients
    else:
        header = "MSG_BROADCAST:" + str(len(text)) + "\n"
        conn.send(header.encode())
        conn.send(text.encode())
        handleMessage(text, username)

# handles data that needs to be processed before program exit
def cleanup(conn, cursesEnabled=True):
    rcvDone = True # tell the async message receive thread to exit

    # close curses window 
    if cursesEnabled:
        curses.nocbreak()
        curses.echo()
        stdscr.nodelay(False)
        curses.endwin()
    # tell the server the client is disconnecting
    conn.send(b"CLOSE\n")
    conn.close()
    exit()

# process a message to be displayed to the user
def handleMessage(text, username):
    global newMessages
    messageLock.acquire()
    
    # display a custom unicast message
    if text.startswith("UNICAST:"):
        msg_len = len(text)
        overflow_rows = (msg_len // width) # number of lines the message occupies
        newMessages.append((text, overflow_rows))
    # display a list of available commands
    elif text.startswith("/help"):
        newMessages.append((HELP_STR, 5))
        newMessages.append(("[Server]: Available commands: ", 0))
    # display the MOTD
    elif text.startswith("/MOTD: "):
        motd = "[Server]: MOTD: " + text[6:]
        newMessages.append((motd, (len(motd)-1) // width))
    # display an emote message
    elif text.startswith("/me "):
        emoteMsg = "/ME: *" + username + " " + text[4:]
        msg_len = len(emoteMsg)-5
        newMessages.append((emoteMsg, msg_len // width)) 
    else: 
        # display a normal broadcast message
        msg_len = len(text)
        overflow_rows = (msg_len // width) # number of rows the message occupies
        msg = "[" + username + "]: " + text # organize the text for rendering

        newMessages.append((msg, overflow_rows))
    messageLock.release()

# a thread that listens for asynchronous messages from the server. This is so the client
#   can display messages and process user input at the same time
def listener(conn):
    conn.listen(2)
    while True:
        conn, clientAddr = conn.accept()
        # infinitely check for messages
        while True:
            if rcvDone:
                conn.close()
                exit(0)
            msg_header = getLineAsync(conn) # wait for a message
            if msg_header != "0":
                colon = msg_header.find(":") # find the colon
                msg_len = msg_header[:colon] # get the length of the message from the header
                sender = msg_header[colon+1:] # get the sender of the message
                msg = getFullMsg(conn, int(msg_len)) # full message
                handleMessage(msg, sender) # process the message
            # sleep so the loop doesn't happen every CPU cycle
            time.sleep(.1)

# properly format the string of usernames for rendering to the client
def formatOnlineUsers(users_str):
    global newMessages
    maxWidth = width - 3 # width the message can occupy
    renderLst = []
    users = users_str.split(",") # generates a list of usernames
    outStr = "   "
    outStrLen = 0

    # re-assemble usernames into strings that fit onto a single line
    for user in users:
        user_len = len(user)
        if user_len + outStrLen + 3 > maxWidth:
            renderLst.append(outStr[:])
            outStr = "   "
            outStrLen = 0
        outStr += user + ", "
        outStrLen += user_len + 2

    renderLst.append(outStr)

    # add users to message list
    messageLock.acquire()
    for msg in renderLst:
        newMessages.append((msg, 0))
    newMessages.append(("[Server]: Users currently online: ", 0))
    messageLock.release()

# render messages so the user can see them
def renderMessages(width, emptyStr):
    global newMessages, allMessages
    messageLock.acquire()
    
    # copy new messages to the main message buffer
    for i in range(len(newMessages)):
        msg = newMessages.pop(-1)
        allMessages.append(msg)
    messageLock.release()
    num_messages = len(allMessages)

    # pointer to the most recent message in message buffer
    msgIndex = num_messages-1

    # clear the screen
    for i in range(height-1, 0, -1):
        stdscr.addstr(i, 0, emptyStr)

    # render images from bottom to top
    renderIndex = height-1
    while renderIndex > 1 and msgIndex >= 0:
        msg_data = allMessages[msgIndex] # get tuple
        msg = msg_data[0] # msg
        renderIndex -= msg_data[1] # data

        # render a unicast message
        if msg.startswith("UNICAST: "):
            msg = msg[9:]
            stdscr.addstr(renderIndex, 0, msg, curses.A_ITALIC)
        # render an error, then delete the error message from the message buffer
        elif msg.startswith("/ERR: "):
            allMessages.pop(msgIndex)
            stdscr.addstr(renderIndex, 0, msg[6:])
        # display fancy emote message
        elif msg.startswith("/ME: "):
            stdscr.addstr(renderIndex, 0, msg[5:], curses.A_BLINK)
        else:
            # display normal broadcast message
            if renderIndex < 0:
                break
            stdscr.addstr(renderIndex, 0, msg)
        msgIndex -= 1
        renderIndex -= 1

# get keyboard input from the user asynchronously
def getUserInput():
    try:
        keyPress = stdscr.getkey()
        return keyPress
    except Exception:
        return "NONE" # no keypress

try:
    # connect to the server
    clientSocket.connect((ip, port))
    clientSocket.send(b"init\n") # send initialization message
    rcv = getLineSync(clientSocket) # check to see if the client is locked out
    if rcv == "ERR_AUTH_LOCKOUT":
        print("You are locked out of this server currently.")
        print("Please wait 2 minutes to try logging in again.")
        clientSocket.close()
        exit()

# check for valid server connection
except BrokenPipeError:
    print("Invalid IP address or port.")
    print("Or, perhaps the server is not available.")
    clientSocket.close()
    exit()
except ConnectionRefusedError:
    print("Invalid IP address or port.")
    print("Or, perhaps the server is not available.")
    clientSocket.close()
    exit()

username = input(f"username for {ip}: ")
password = input("password: ")

# delimit data with newlines
usernameData = username + "\n"
passwordData = password + "\n"
portData = str(listenerPort) + "\n"

# send username and password
clientSocket.send(usernameData.encode())
clientSocket.send(passwordData.encode())
clientSocket.send(portData.encode())

# check for authentication status
status = getLineSync(clientSocket)
if status == "AUTH_FAIL":
    print("Invalid username or password.")
    cleanup(clientSocket, cursesEnabled=False)
elif status == "ERR_CONCURRENT_CONNECTION":
    print("You are already connected to this chat room via another chat client.")
    print("Simultaneous connections are not allowed.")
    cleanup(clientSocket, cursesEnabled=False)
elif status == "AUTH_LOCKOUT":
    print("You have failed to authenticate too many times in 30 seconds.")
    print("Please wait 2 minutes to be able to log in again.")
    cleanup(clientSocket, cursesEnabled=False)
elif status == "AUTH_GOOD":
    print(f"Welcome, {username}! Loading chatroom...")
    time.sleep(1.5)

# init curses
stdscr = curses.initscr()
curses.cbreak() # capture keys before <enter>
stdscr.nodelay(True) # async keyboard input

# create the main screen
emptyStr = " "*width
for row in range(1, height):
    stdscr.addstr(row, 0, emptyStr[:])
stdscr.addstr(height,0, "+"+"-"*(width)+"+")
stdscr.addstr(height+1, 0, "")

# contains the current text the user typed
userText = ""

# start the async message receive thread
threading.Thread(target=listener, args=(rcvListener,), daemon=True).start() 

running = True
try:
    # keep looping until the client exits
    while running:
        keyPress = getUserInput()
        # clear the line and erase a character when a backspace is detected
        if keyPress in ('KEY_BACKSPACE', '\b', '\x7f'):
            userText = userText[:-1]
            stdscr.addstr(height+1, 0, emptyStr)    
            stdscr.addstr(height+1, 0, userText)   
        # process user input when a newline is detected
        elif keyPress == "\n":
            stdscr.addstr(height+1, 0, emptyStr)
            handleCommand(userText, clientSocket, username)
            userText = ""
            keyPress = ""
        # add a new keypress to user text 
        elif keyPress != "NONE":
            userText += keyPress
        stdscr.addstr(height+1, 0, userText)

        # re-render messages only when new ones arrive
        if len(newMessages) > 0:
            renderMessages(width, emptyStr)
        

        # sleep so the loop doesn't happen every CPU cycle
        time.sleep(.01)

# if an exception occurs, exit curses so the user's terminal doesn't get messed up
except Exception as e:
    stdscr.addstr(height+2, 0, "Error occurred, ensuring curses closes")
    cleanup(clientSocket)

except KeyboardInterrupt:
    cleanup(clientSocket)
