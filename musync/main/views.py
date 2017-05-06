from __future__ import division
from django.shortcuts import render

# Create your views here.

import librosa , pydub ,requests
import numpy as np
from tempfile import TemporaryFile
import pickle, json, os
from django.http import HttpResponse

class mash:

    def __init__(self, json_, cached=False):
        self.sr = 22050  # new Sampling Rate for the audio files

        self.songs = json_
        self.Yin = []
        self.Yout = []
        self.pathIn = []
        self.pathOut = []
        self.beats = {'in': [], 'out': []}
        self.tempo = {'in': 0, 'out': 0}

        self._setup()
        self._load(cached=cached)
        self._extract()
        self._segment()
        self._speedUp()
        out = self._mix()

        print "Exporting..."
        out.export(out_f="final.mp3", format="mp3")
        print "[SUCCESS] Export as `final.mp3`"

    def _setup(self):
        if not os.path.exists('cache'):
            os.makedirs('cache')

    def _load(self, cached=True):
        for song in self.songs:
            if os.path.exists("cache/%s.pkl"%song['name']):
                print "\nLoading", song['name'], "from cache"
                with open("cache/%s.pkl"%song['name'], 'rb') as f:
                    if song['mixin']:
                        print "Yin=", song['name']
                        self.Yin = pickle.load(f)
                        self.pathIn = song['path']
                    else:
                        print "Yout=", song['name']
                        self.Yout.append(pickle.load(f))
                        self.pathOut.append(song['path'])
                continue

            print "\nLoading", song['name']
            y, sr = librosa.load(song['path'], sr=self.sr)
            if song['mixin']:
                self.Yin = y
                self.pathIn = song['path']
            else:
                self.Yout.append(y)
                self.pathOut.append(song['path'])
            print "[SUCCESS] Loaded", song['name']

            if cached:
                try:
                    with open('cache/%s.pkl'%song['name'], 'wb') as f:
                        pickle.dump(y, f)
                        print "[SUCCESS] Cached", song['name']
                except Exception as e:
                    print "[FAILED] Caching", song['name']
                    print e

    def _extract(self):
        # TODO: Add cosine distance similarity to choose the best mixout
        self.Yout = self.Yout[0] # NOTE: considering 1mixin & 1mixout
        self.pathOut = self.pathOut[0]

        self.tempo['in'], self.beats['in'] = librosa.beat.beat_track(y=self.Yin, sr=self.sr)
        self.tempo['out'], self.beats['out'] = librosa.beat.beat_track(y=self.Yout, sr=self.sr)

        print "TempoIn=", self.tempo['in']
        print "TempoOut=", self.tempo['out']

        self._OTAC()
        self._crossFadeRegion()

    def _OTAC(self): # Optimal Tempo Adjustment Coefficient Computation
        C = [-2, -1, 0, 1, 2]

        if self.tempo['in'] == self.tempo['out']:
            self.tempo['tgt'] = self.tempo['in']
            return

        Tin_ = [(2**c)*self.tempo['in'] for c in C]
        TinIndex_ = np.argmin(np.absolute(Tin_ - self.tempo['out']))
        Copt = C[TinIndex_]
        Bopt = (2**Copt)*self.tempo['in']

        Tlow = min(Bopt, self.tempo['out'])
        Thigh = max(Bopt, self.tempo['out'])

        a, b = 0.765, 1
        Ttgt = (a-b)*Tlow + np.sqrt( ((a-b)**2)*(Tlow**2) + 4*a*b*Thigh*Tlow )
        Ttgt = Ttgt/(2*a)

        print "FoptIn=", Ttgt/Bopt
        print "FoptOut=", Ttgt/self.tempo['out']
        print "Ttgt=", Ttgt

        self.tempo['tgt'] = Ttgt

    def _crossFadeRegion(self): # Computes the cross fade region for the mixed song
        Na = self.beats['in'].shape[0]-1

        scores = [self._score(i, Na) for i in xrange(2, int(Na/4))]
        noBeats = np.argmax(scores)+2

        inDuration = librosa.get_duration(y=self.Yin, sr=self.sr)
        fadeInStart = librosa.frames_to_time(self.beats['in'], sr=self.sr)[-int(noBeats/2)]
        fadeIn = inDuration - fadeInStart

        fadeOut = librosa.frames_to_time(self.beats['out'], sr=self.sr)[int(noBeats/2)]

        print "Best Power Corelation Scores=", np.max(scores)
        print "Number of beats in cross fade region=", noBeats
        print "fadeInStart=", fadeInStart
        print "fadeOutEnd=", fadeOut
        print "Cross Fade Time=", fadeIn+fadeOut

        self.crossFade = [fadeInStart*1000, fadeOut*1000] # In milliseconds


    def _score(self, T, Na):
        cr = 0
        for i in xrange(1, T+1):
            cr += self.beats['in'][Na-i+1]*self.beats['out'][i]
        return cr/T

    def _segment(self):
        print "Started Segmentation"
        

        pydub.AudioSegment.ffmpeg = "/usr/local/bin/ffmpeg"

        sIn = pydub.AudioSegment.from_file(self.pathIn, format="mp3")
        sOut = pydub.AudioSegment.from_file(self.pathOut, format="mp3")


        # sIn= subprocess.Popen(['mpg123','-q',self.pathIn]).wait()
        # sOut = subprocess.Popen(['mpg123','-q',self.pathOut]).wait()
        print "[SUCCESS] Segmented audio files"

        self.segments = {
            'in': [ sIn[:self.crossFade[0]], sIn[self.crossFade[0]:] ],
            'out': [ sOut[:self.crossFade[1]], sOut[self.crossFade[1]:] ],
        }
        del sIn, sOut

    def _speedUp(self):
        s1 = self.segments['in'][1]
        s2 = self.segments['out'][0]

        speed1 = self.tempo['tgt']/self.tempo['in']
        speed2 = self.tempo['tgt']/self.tempo['out']

        print "Playback Speed of in end segment=",speed1,'X'
        print "Playback Speed of out start segment=",speed2,'X'

        s1 = s1.speedup(playback_speed=speed1)
        s2 = s1.speedup(playback_speed=speed2)

    def _mix(self):
        xf = self.segments['in'][1].fade(to_gain=-120, start=0, end=float('inf'))
        xf *= self.segments['out'][0].fade(from_gain=-120, start=0, end=float('inf'))

        out = TemporaryFile()

        out.write(self.segments['in'][0]._data)
        out.write(xf._data)
        out.write(self.segments['out'][1]._data)

        out.seek(0)

        print "[SUCCESS] Mixed 4 audio segment to 1"
        return self.segments['in'][0]._spawn(data=out)

def song(request):
	# print os.getcwd()
    with open('static/main/songs.json', 'r') as f:
        j = json.loads(f.read())
        obj = mash(j, cached=True)

    return HttpResponse("Succcessful")































