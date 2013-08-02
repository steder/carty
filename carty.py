#!/usr/bin/env python3
"""
Carty hungee!

"""
import collections
import logging
import pprint
import ssl
import sys

import redis
from sleekxmpp import ClientXMPP
#from sleekxmpp.exceptions import IqError, IqTimeout

import settings


class CartyBot(ClientXMPP):
    def __init__(self, jid, password, nick, short_nick, dbconf):
        ClientXMPP.__init__(self, jid, password)

        # This has to be the fullname on hipchat
        self.jid = jid
        self.nick = nick
        self.short_nick = short_nick
        self.dbhost = dbconf['HOST']
        self.dbport = dbconf['PORT']
        self.db = redis.Redis(self.dbhost, self.dbport)

        # plusplus data:

        self.upvotes = collections.Counter(
            {k: int(v) for k,v in self.db.hgetall("upvotes").items()}
        )
        self.downvotes = collections.Counter(
            {k: int(v) for k,v in self.db.hgetall("downvotes").items()}
        )
        self.upvote_reasons = collections.defaultdict(list)
        self.upvote_reasons.update(self.db.hgetall("upvote_reasons"))
        self.downvote_reasons = collections.defaultdict(list)
        self.downvote_reasons.update(self.db.hgetall("downvote_reasons"))

        # If you wanted more functionality, here's how to register plugins:
        self.register_plugin('xep_0030') # Service Discovery
        self.register_plugin('xep_0045') # Multi-User Chat
        self.register_plugin('xep_0199') # XMPP Ping
        self.register_plugin('xep_0249') # Direct MUC Invitations

        # handlers
        self.add_event_handler("session_start",
                               self.session_start)
        self.add_event_handler("message",
                               self.message)
        self.add_event_handler("groupchat_direct_invite",
                               self.handle_direct_invite)
        self.add_event_handler("groupchat_invite",
                               self.handle_invite)
        self.add_event_handler("groupchat_message",
                               self.muc_message)

        # If you are working with an OpenFire server, you will
        # need to use a different SSL version:
        # TODO: i don't know if this is actually bumping the ssl version
        self.ssl_version = ssl.PROTOCOL_SSLv3

    def session_start(self, event):
        self.send_presence()
        self.get_roster()

        # Most get_*/set_* methods from plugins use Iq stanzas, which
        # can generate IqError and IqTimeout exceptions
        #
        # try:
        #     self.get_roster()
        # except IqError as err:
        #     logging.error('There was an error getting the roster')
        #     logging.error(err.iq['error']['condition'])
        #     self.disconnect()
        # except IqTimeout:
        #     logging.error('Server is taking too long to respond')
        #     self.disconnect()

    def message(self, msg):
        logging.info("GOT MSG")
        if msg['type'] in ('chat', 'normal'):
            msg.reply("Thanks for sending\n%(body)s" % msg).send()
            #self._handle_message_to_me(msg)

    def handle_invite(self, msg):
        logging.info("GOT INVITE")
        self['xep_0045'].joinMUC(msg["from"], self.nick, maxhistory="1", wait=True)

    def handle_direct_invite(self, msg):
        logging.info("GOT DIRECT INVITE")

    def _handle_command(self, msg):
        """
        Returns True if the msg was indeed a command that we could handle.
        """
        logging.info("HANDLE COMMAND")
        # maybe see if we can find a command in this message and handle it
        body = msg['body'].lower()
        if body.startswith("!scores"):
            logging.info("COMMAND CONTAINS SCORE")
            scores = collections.defaultdict(int)
            for k, v in self.upvotes.items():
                scores[k] += v
            for k, v in self.downvotes.items():
                scores[k] -= v
            scores = collections.OrderedDict(
                (k, v) for k, v in sorted(scores.items(),
                                          key=lambda x: scores[x])
            )
            reply = msg.reply("scoreboard:\n{}".format("\n".join("{0}: {1}".format(k, v) for k,v in scores.items())))
            reply.send()
            return True
        elif body.startswith("!score"):
            _, target = body.split("!score", 1)
            target = target.strip()
            score = self.upvotes[target] - self.downvotes[target]
            upvotes = self.upvotes[target]
            downvotes = self.downvotes[target]
            msg.reply(
                "score for '{}' is '{}' with {} upvotes and {} downvotes".format(
                    target, score, upvotes, downvotes)
            ).send()
        # TODO: This works a little too well.  I can't get carty to rejoin a room.
        # I suspect that maybe the leaveMUC function in the xep 0045 doesn't do
        # everything required to really leave a room:
        #
        #elif body.startswith("!begone"):
        #    try:
        #        self['xep_0045'].leaveMUC(msg['from'], self.nick)
        #    except KeyError:
        #        pass
        #
        # On the other hand this !die command totally works but
        # I think people will spend all their time killing my bot
        # so I won't actually enable this.  Yet.
        #elif body.startswith("!die"):
        #    raise KeyboardInterrupt("Received !die command")

        logging.info("UNHANDLED COMMAND")

    def _handle_message_to_me(self, msg):
        # maybe see if I can respond in some flippant way?
        pass

    def muc_message(self, msg):
        original_body = msg['body']
        body = msg['body'].lower()
        if msg['mucnick'] != self.nick:
            logging.info("MUC MESSAGE")

            # first see if it's a command
            handled = self._handle_command(msg)
            if handled:
                return

            # karma stuff:
            if "++" in original_body:
                key, reason = original_body.split("++", 1)
                key = key.strip()
                reason = reason.strip()
                self.upvotes[key] += 1
                self.upvote_reasons[key].append(reason)
                self.db.hmset("upvotes", self.upvotes)
                self.db.hmset("upvote_reasons", self.upvote_reasons)
                logging.info("Registered upvote for '%s' with reason '%s'"%(key, reason))
                return

            if "--" in original_body:
                key, reason = original_body.split("--", 1)
                key = key.strip()
                reason = reason.strip()
                self.downvotes[key] += 1
                self.downvote_reasons[key].append(reason)
                self.db.hmset("downvotes", self.downvotes)
                self.db.hmset("downvote_reasons", self.downvote_reasons)
                logging.info("Registered downvote for '%s' with reason '%s'"%(key, reason))
                return

            # finally just try handling it as a generic message;
            if self.nick.lower() in body or self.short_nick.lower() in body:
                #if msg['type'] in ('groupchat',) and msg['from'] != self.jid:
                logging.info("MUC MESSAGE TO ME")
                #msg.reply("Thanks for sending\n%(body)s" % msg).send()
                self._handle_message_to_me(msg)


if __name__ == '__main__':
    # Ideally use optparse or argparse to get JID,
    # password, and log level.
    logging.basicConfig(level=logging.DEBUG,
                        format='%(levelname)-8s %(message)s')
    xmpp = CartyBot(settings.JID,
                    settings.PASSWORD,
                    settings.FULLNAME,
                    settings.MENTION,
                    settings.REDIS)
    xmpp.connect()
    xmpp.process(block=True)
