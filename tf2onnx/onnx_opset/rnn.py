# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT license.

"""
rnn
"""

from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import numpy as np
from tf2onnx import utils
from tf2onnx.handler import tf_op

logger = logging.getLogger(__name__)


# pylint: disable=unused-argument,missing-docstring,invalid-name

@tf_op("LSTMBlockCell")
class LSTMBlockCell:
    @classmethod
    def version_1(cls, ctx, node, **kwargs):
        """
        Args:
          x: A `Tensor`. Must be one of the following types: `float32`.
            The input to the LSTM cell, shape (batch_size, num_inputs).
          cs_prev: A `Tensor`. Must have the same type as `x`.
            Value of the cell state at previous time step.
          h_prev: A `Tensor`. Must have the same type as `x`.
            Output of the previous cell at previous time step.
          w: A `Tensor`. Must have the same type as `x`. The weight matrix.
          wci: A `Tensor`. Must have the same type as `x`.
            The weight matrix for input gate peephole connection.
          wcf: A `Tensor`. Must have the same type as `x`.
            The weight matrix for forget gate peephole connection.
          wco: A `Tensor`. Must have the same type as `x`.
            The weight matrix for output gate peephole connection.
          b: A `Tensor`. Must have the same type as `x`. The bias vector.
          forget_bias: An optional `float`. Defaults to `1`. The forget gate bias.
          cell_clip: An optional `float`. Defaults to `-1` (no clipping).
            Value to clip the 'cs' value to. Disable by setting to negative value.
          use_peephole: An optional `bool`. Defaults to `False`.
            Whether to use peephole weights.
          name: A name for the operation (optional).
        Returns:
          A tuple of `Tensor` objects (i, cs, f, o, ci, co, h).
          i: A `Tensor`. Has the same type as `x`. The input gate.
          cs: A `Tensor`. Has the same type as `x`. The cell state before the tanh.
          f: A `Tensor`. Has the same type as `x`. The forget gate.
          o: A `Tensor`. Has the same type as `x`. The output gate.
          ci: A `Tensor`. Has the same type as `x`. The cell input.
          co: A `Tensor`. Has the same type as `x`. The cell after the tanh.
          h: A `Tensor`. Has the same type as `x`. The output h vector.
        ```python
        xh = [x, h_prev]
        [i, ci, f, o] = xh * w + b
        f = f + forget_bias
        if not use_peephole:
          wci = wcf = wco = 0
        i = sigmoid(cs_prev .* wci + i)
        f = sigmoid(cs_prev .* wcf + f)
        ci = tanh(ci)
        cs = ci .* i + cs_prev .* f
        cs = clip(cs, cell_clip)
        o = sigmoid(cs * wco + o)
        co = tanh(cs)
        h = co .* o
        ```
        """
        nodes = []
        x, cs_prev, h_prev, w, wci, wcf, wco, b = node.input
        forget_bias = float(node.get_attr("forget_bias").f)
        cell_clip = float(node.get_attr("cell_clip").f)
        use_peephole = bool(node.get_attr("use_peephole").i)

        def make_sigmoid(i, w, b):
            i_w_node = ctx.make_node("Mul", [i, w])
            i_w_b_node = ctx.make_node("Add", [i_w_node.output[0], b])
            output_node = ctx.make_node("Sigmoid", [i_w_b_node.output[0]])
            nodes.extend([i_w_node, i_w_b_node, output_node])
            return output_node.output[0]

        # xh = [x, h]
        xh_node = ctx.make_node("Concat", [x, h_prev], attr={"axis": 1})

        # i, ci, f, o = xh * w + b
        xh_w_node = ctx.make_node("MatMul", [xh_node.output[0], w])
        w_shape = ctx.get_shape(w)
        if len(w_shape) != 2 or w_shape[1] % 4 != 0:
            raise RuntimeError("shape of W of LSTMBlockCell {} should be times of 4".format(node.name))
        merged_output_node = ctx.make_node("Add", [xh_w_node.output[0], b])
        w_last_dim = int(w_shape[1] / 4)
        split = [w_last_dim] * 4
        split_output_node = ctx.make_node(
            "Split", [merged_output_node.output[0]],
            attr={"axis": 1, "split": split},
            output_count=4
        )
        i, ci, f, o = split_output_node.output

        # f = f + forget_bias
        forget_bias_const = ctx.make_const(
            utils.make_name("{}__forget_bias".format(node.name)),
            np.array(forget_bias, dtype=np.float32)
        )
        f_node = ctx.make_node("Add", [f, forget_bias_const.output[0]])

        if not use_peephole:
            zeros_const = ctx.make_const(
                utils.make_name("{}__zeros_const".format(node.name)),
                np.zeros([w_last_dim], dtype=np.float32)
            )
            nodes.append(zeros_const)
            wci = zeros_const.output[0]
            wcf = zeros_const.output[0]
            wco = zeros_const.output[0]

        # i = sigmoid(cs_prev .* wci + i)
        i = make_sigmoid(cs_prev, wci, i)
        # f = sigmoid(cs_prev .* wcf + f)
        f = make_sigmoid(cs_prev, wcf, f_node.output[0])
        # ci = Tanh(ci)
        ci_node = ctx.make_node("Tanh", [ci])
        # cs = ci .* i + f .* cs_prev
        ci_i_node = ctx.make_node("Mul", [ci_node.output[0], i])
        cs_prev_f_node = ctx.make_node("Mul", [cs_prev, f])
        cs_node = ctx.make_node("Add", [ci_i_node.output[0], cs_prev_f_node.output[0]])
        cs = cs_node.output[0]
        # cs = clip(cs)
        if cell_clip > 0:
            if ctx.opset < 11:
                cs_clip_node = ctx.make_node("Clip", [cs], attr={"max": cell_clip, "min": -cell_clip})
                nodes.append(cs_clip_node)
                cs = cs_clip_node.output[0]
            else:
                dtype = utils.map_onnx_to_numpy_type(ctx.get_dtype(cs))
                name_min = utils.make_name("{}_min".format(node.name))
                name_max = utils.make_name("{}_max".format(node.name))
                min_const = ctx.make_const(name_min, np.array(-cell_clip, dtype=dtype))
                max_const = ctx.make_const(name_max, np.array(cell_clip, dtype=dtype))
                cs_clip_node = ctx.make_node('Clip', [cs, min_const.output[0], max_const.output[0]])
                nodes.append(cs_clip_node)
                cs = cs_clip_node.output[0]

        # o = cs * wco + o
        o = make_sigmoid(cs, wco, o)
        # co = Tanh(cs)
        co_node = ctx.make_node("Tanh", [cs])
        # h = co .* o
        h_node = ctx.make_node("Mul", [co_node.output[0], o])

        def replace_output(old_output, new_output):
            ctx.replace_all_inputs(ctx.get_nodes(), old_output, new_output)
            ctx.copy_dtype(old_output, new_output)
            ctx.copy_shape(old_output, new_output)

        replace_output(node.output[0], i)
        replace_output(node.output[1], cs)
        replace_output(node.output[2], f)
        replace_output(node.output[3], o)
        replace_output(node.output[4], ci_node.output[0])
        replace_output(node.output[5], co_node.output[0])
        replace_output(node.output[6], h_node.output[0])

    @classmethod
    def version_7(cls, ctx, node, **kwargs):
        cls.version_1(ctx, node, **kwargs)


@tf_op("CudnnRNN")
class CudnnRNN:
    @classmethod
    def version_11(cls, ctx, node, **kwargs):
        X = node.input[0]
        X_shape = ctx.get_shape(X)
        H = node.input[1]
        H_shape = ctx.get_shape(H)
        P = node.input[3]
        utils.make_sure(
            node.attr["rnn_mode"].s == b"gru",
            "rnn mode other than gru are not supported yet"
        )
        utils.make_sure(
            node.attr["dropout"].f == 0,
            "dropout not supported yet"
        )
        utils.make_sure(
            node.attr["input_mode"].s == b"linear_input",
            "input mode must be linear input"
        )
        num_dirs = 1 if node.attr["direction"].s == b"unidirectional" else 2
        num_layers = int(H_shape[0]/num_dirs)
        num_units = hidden_size = H_shape[2]
        input_size = X_shape[2]
        w_shape = [num_layers*num_dirs, 3*hidden_size, input_size]
        w_shape_const = ctx.make_const(utils.make_name("w_shape"), np.array(w_shape, dtype=np.int64))
        r_shape = [num_layers*num_dirs, 3*hidden_size, hidden_size]
        r_shape_const = ctx.make_const(utils.make_name("r_shape"), np.array(r_shape, dtype=np.int64))
        b_shape = [num_layers*num_dirs, 6*hidden_size]
        b_shape_const = ctx.make_const(utils.make_name("b_shape"), np.array(b_shape, dtype=np.int64))
        zero_const = ctx.make_const(utils.make_name("zero"), np.array([0], dtype=np.int64))
        w_end = np.prod(w_shape)
        w_end_const = ctx.make_const(utils.make_name("w_end"), np.array([w_end], dtype=np.int64))
        r_end = w_end + np.prod(r_shape)
        r_end_const = ctx.make_const(utils.make_name("r_end"), np.array([r_end], dtype=np.int64))
        b_end = r_end + np.prod(b_shape)
        b_end_const = ctx.make_const(utils.make_name("b_end"), np.array([b_end], dtype=np.int64))
        def NM(nm):
            return node.name + "_" + nm
        WS = [NM('W_' + str(i)) for i in range(num_layers*num_dirs)]
        RS = [NM('R_' + str(i)) for i in range(num_layers*num_dirs)]
        BS = [NM('B_' + str(i)) for i in range(num_layers*num_dirs)]
        HS = [NM('H_' + str(i)) for i in range(num_layers*num_dirs)]
        YHS = [NM('YH_' + str(i)) for i in range(num_layers*num_dirs)]
        W_flattened = ctx.make_node('Slice', [P, zero_const.output[0], w_end_const.output[0]])
        R_flattened = ctx.make_node('Slice', [P, w_end_const.output[0], r_end_const.output[0]])
        B_flattened = ctx.make_node('Slice', [P, r_end_const.output[0], b_end_const.output[0]])
        W = utils.make_name('W')
        R = utils.make_name('R')
        B = utils.make_name('B')
        ctx.make_node('Reshape', [W_flattened.output[0], w_shape_const.output[0]], outputs=[W])
        ctx.make_node('Reshape', [R_flattened.output[0], r_shape_const.output[0]], outputs=[R])
        ctx.make_node('Reshape', [B_flattened.output[0], b_shape_const.output[0]], outputs=[B])
        ctx.make_node('Split', [W], outputs=WS)
        ctx.make_node('Split', [R], outputs=RS)
        ctx.make_node('Split', [B], outputs=BS)
        ctx.make_node('Split', [H], outputs=HS)
        XNF = XNB = X
        for i in range(num_layers):
            suffix = '_' + str(i*num_dirs)
            ctx.make_node('GRU', [XNF, NM('W' + suffix), NM('R' + suffix), NM('B' + suffix), '', NM('H'+ suffix)],
                          outputs=[NM('Y' + suffix), NM('YH' + suffix)],
                          attr={'direction': 'forward', 'hidden_size': num_units})
            XNF = NM(X + suffix)
            ctx.make_node('Squeeze', [NM('Y' + suffix)], outputs=[XNF], attr={'axes': [1]})
            if num_dirs == 2:
                suffix = '_' + str(i*2+1)
                ctx.make_node('GRU', [XNB, NM('W' + suffix), NM('R' + suffix), NM('B' + suffix), '', NM('H'+ suffix)],
                              outputs=[NM('Y' + suffix), NM('YH' + suffix)],
                              attr={'direction': 'reverse', 'hidden_size': num_units})
                XNB = NM(X + suffix)
                ctx.make_node('Squeeze', [NM('Y' + suffix)], outputs=[XNB], attr={'axes': [1]})
        ctx.remove_node(node.name)
        if num_dirs == 2:
            ctx.make_node('Concat', [XNF, XNB], outputs=[node.output[0]], attr={'axis': -1})
        else:
            ctx.make_node('Identity', [XNF], outputs=[node.output[0]])
        ctx.make_node('Concat', YHS, outputs=[node.output[1]], attr={'axis': 0})
