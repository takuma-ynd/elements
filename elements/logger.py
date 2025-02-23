import json
import pathlib
import time

import numpy as np

def disable_gpu():
  import tensorflow as tf
  try:
    # Disable all GPUS
    tf.config.set_visible_devices([], 'GPU')
    visible_devices = tf.config.get_visible_devices()
    for device in visible_devices:
        assert device.device_type != 'GPU'
  except:
    # Invalid device or cannot modify virtual devices once initialized.
    pass

class Logger:

  def __init__(self, step, outputs, multiplier=1):
    self._step = step
    self._outputs = outputs
    self._multiplier = multiplier
    self._last_step = None
    self._last_time = None
    self._metrics = []

  def add(self, mapping, prefix=None, val_type=None):
    step = int(self._step) * self._multiplier
    for name, value in dict(mapping).items():
      name = f'{prefix}_{name}' if prefix else name
      value = np.array(value)

      # Guess val_type
      if val_type is None:
        if len(value.shape) == 0:
          val_type = 'scalar'
        elif len(value.shape) in [2, 3]:
          val_type = 'image'
        elif len(value.shape) == 4:
          val_type = 'video'
        else:
          raise ValueError(f'invalid value.shape: {value.shape}\nIf you intend to log histograms, explicitly specify val_type.')

      self._metrics.append((step, name, value, val_type))

  def scalar(self, name, value):
    self.add({name: value})

  def image(self, name, value):
    self.add({name: value})

  def video(self, name, value):
    self.add({name: value})

  def write(self, fps=False):
    fps and self.scalar('fps', self._compute_fps())
    if not self._metrics:
      return
    for output in self._outputs:
      output(self._metrics)
    self._metrics.clear()

  def _compute_fps(self):
    step = int(self._step) * self._multiplier
    if self._last_step is None:
      self._last_time = time.time()
      self._last_step = step
      return 0
    steps = step - self._last_step
    duration = time.time() - self._last_time
    self._last_time += duration
    self._last_step = step
    return steps / duration


class TerminalOutput:

  def __call__(self, summaries):
    step = max(s for s, _, _, _ in summaries)
    scalars = {k: float(v) for _, k, v, _ in summaries if len(v.shape) == 0}
    formatted = {k: self._format_value(v) for k, v in scalars.items()}
    print(f'[{step}]', ' / '.join(f'{k} {v}' for k, v in formatted.items()))

  def _format_value(self, value):
    if value == 0:
      return '0'
    elif 0.01 < abs(value) < 10000:
      value = f'{value:.2f}'
      value = value.rstrip('0')
      value = value.rstrip('0')
      value = value.rstrip('.')
      return value
    else:
      value = f'{value:.1e}'
      value = value.replace('.0e', 'e')
      value = value.replace('+0', '')
      value = value.replace('+', '')
      value = value.replace('-0', '-')
    return value


class JSONLOutput:

  def __init__(self, logdir):
    self._logdir = pathlib.Path(logdir)

  def __call__(self, summaries):
    scalars = {k: float(v) for _, k, v, _ in summaries if len(v.shape) == 0}
    step = max(s for s, _, _, _ in summaries)
    with (self._logdir / 'metrics.jsonl').open('a') as f:
      f.write(json.dumps({'step': step, **scalars}) + '\n')


class TensorBoardOutput:

  def __init__(self, logdir, fps=20):
    import tensorflow as tf
    disable_gpu()
    self._writer = tf.summary.create_file_writer(str(logdir), max_queue=1000)
    self._fps = fps

  def __call__(self, summaries):
    import tensorflow as tf
    self._writer.set_as_default()
    for step, name, value, val_type in summaries:
      if val_type == 'scalar':
        tf.summary.scalar('scalars/' + name, value, step)
      elif val_type == 'image':
        tf.summary.image(name, value, step)
      elif val_type == 'video':
        self._video_summary(name, value, step)
      elif val_type == 'histogram':
        tf.summary.histogram(name, value, step)
    self._writer.flush()

  def _video_summary(self, name, video, step):
    import tensorflow as tf
    import tensorflow.compat.v1 as tf1
    name = name if isinstance(name, str) else name.decode('utf-8')
    if np.issubdtype(video.dtype, np.floating):
      video = np.clip(255 * video, 0, 255).astype(np.uint8)
    try:
      T, H, W, C = video.shape
      summary = tf1.Summary()
      image = tf1.Summary.Image(height=H, width=W, colorspace=C)
      image.encoded_image_string = encode_gif(video, self._fps)
      summary.value.add(tag=name, image=image)
      tf.summary.experimental.write_raw_pb(summary.SerializeToString(), step)
    except (IOError, OSError) as e:
      print('GIF summaries require ffmpeg in $PATH.', e)
      tf.summary.image(name, video, step)


def encode_gif(frames, fps):
  from subprocess import Popen, PIPE
  h, w, c = frames[0].shape
  pxfmt = {1: 'gray', 3: 'rgb24'}[c]
  cmd = ' '.join([
      'ffmpeg -y -f rawvideo -vcodec rawvideo',
      f'-r {fps:.02f} -s {w}x{h} -pix_fmt {pxfmt} -i - -filter_complex',
      '[0:v]split[x][z];[z]palettegen[y];[x]fifo[x];[x][y]paletteuse',
      f'-r {fps:.02f} -f gif -'])
  proc = Popen(cmd.split(' '), stdin=PIPE, stdout=PIPE, stderr=PIPE)
  for image in frames:
    proc.stdin.write(image.tobytes())
  out, err = proc.communicate()
  if proc.returncode:
    raise IOError('\n'.join([' '.join(cmd), err.decode('utf8')]))
  del proc
  return out
