from dataclasses import dataclass
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Start, Stream, Dial, Conference, Play

@dataclass
class TelephonyConfig:
    account_sid: str
    auth_token: str
    from_number: str
    public_base_url: str  # the ngrok link




class Telephony:
    def __init__(self, cfg: TelephonyConfig):
        self.cfg = cfg
        self.client = Client(cfg.account_sid, cfg.auth_token)


    #call the given number
    # Twilio will request TwiML from twiml_url to know what to do next.
        #as soon as the call connects to the menu screen, 
        # twilio visits the given link , the link will tell twilio to connect this call to websocket so gemini can interact
    # returns call SID ; an identifier
    def start_outbound_call(self, to_number: str, twiml_url: str) -> str:
        """
        Create outbound call to the target number (business/IVR).
        Twilio will request TwiML from twiml_url to know what to do next.
        Returns call SID.
        """
        call = self.client.calls.create(
            to=to_number,
            from_=self.cfg.from_number,
            url=twiml_url,
        )
        return call.sid


    def call_user_and_join(self, user_number: str, twiml_url: str) -> str:
        """
        Call the user's phone number and join them into the conference.
        Returns call SID for the user leg.
        """
        call = self.client.calls.create(
            to=user_number,
            from_=self.cfg.from_number,
            url=twiml_url,
        )
        return call.sid

    def send_dtmf(self, call_sid: str, digits: str):
        """
        Send DTMF digits to an in-progress call.
        This is how the agent "presses buttons" in the IVR.
        """
        self.client.calls(call_sid).update(send_digits=digits)

    def hangup(self, call_sid: str):
        """End a call leg."""
        self.client.calls(call_sid).update(status="completed")



def build_twiml_outbound(ws_media_url: str, conference_name: str) -> str:
    vr = VoiceResponse()

    # Start Twilio Media Streams -> your WebSocket
    start = Start()
    start.stream(url=ws_media_url)
    vr.append(start)

    # Put the business leg into a conference
    dial = Dial()
    dial.conference(
        conference_name,
        start_conference_on_enter=True,
        end_conference_on_exit=True,
    )
    vr.append(dial)

    return str(vr)



def build_twiml_join_user(conference_name: str, handoff_mp3_url: str | None) -> str:
    vr = VoiceResponse()

    if handoff_mp3_url:
        vr.play(handoff_mp3_url)

    dial = Dial()
    dial.conference(
        conference_name,
        start_conference_on_enter=True,
        end_conference_on_exit=True,
    )
    vr.append(dial)

    return str(vr)

