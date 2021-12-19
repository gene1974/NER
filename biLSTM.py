import sys
import torch
import torch.nn as nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from crf import CRF
from CharEmbedding import CharEmbedding
from WordEmbedding import WordEmbedding
from LMEmbedding import LMEmbedding
from AttentionPooling import AttentionPooling
from Utils import *

class BiLSTM_CRF(nn.Module):
    def __init__(self, word_vocab, tag_vocab, 
            char_emb_dim, hidden_dim, num_layers, 
            batch_size, device, dropout = 0.5, 
            use_pretrained = True, use_word = True, use_char = True, use_lm = True, use_crf = True, use_cnn = True, 
            attention_pooling = False):
        super(BiLSTM_CRF, self).__init__()
        self.word_vocab = word_vocab
        self.tag_vocab = tag_vocab

        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.tagset_size = len(tag_vocab.tag_to_ix)
        self.charset_size = len(word_vocab.char_to_ix)
        
        self.batch_size = batch_size
        self.device = device
        self.use_pretrained = use_pretrained
        self.use_word = use_word
        self.use_char = use_char
        self.use_cnn = use_cnn
        self.use_crf = use_crf
        self.use_lm = use_lm
        self.attention_pooling = attention_pooling
        
        self.char_emb_dim = char_emb_dim
        self.emb_dim = 0
        if use_word:
            if use_pretrained:
                self.word_emb_dim = word_vocab.word_emb.shape[1]
            else:
                self.word_emb_dim = char_emb_dim
            self.word_embeds = WordEmbedding(word_vocab, use_pretrained, self.word_emb_dim)
            self.emb_dim += self.word_emb_dim
        if use_char:
            self.char_embeds = CharEmbedding(self.charset_size, char_emb_dim, use_cnn, attention_pooling)
            self.emb_dim += char_emb_dim 
        
        if use_lm:
            self.lm_embeds = LMEmbedding()
            self.lm_emb_dim = self.lm_embeds.get_emb_dim()
            self.emb_dim += self.lm_emb_dim
        if attention_pooling:
            self.atten_pool = AttentionPooling()
        
        self.dropout1 = nn.Dropout(p = dropout)
        self.dropout2 = nn.Dropout(p = dropout)
        self.lstm = nn.LSTM(self.emb_dim, hidden_dim // 2,
                            num_layers = num_layers, bidirectional=True)
        self.hidden2tag = nn.Linear(hidden_dim, self.tagset_size)
        if use_crf:
            self.crf = CRF(self.tag_vocab.ix_to_tag)
        
    def _init_weight(self):
        for name, param in self.lstm.named_parameters():
            if name.startswith('bias'): # b_i|b_f|b_g|b_o
                nn.init.zeros_(param)
                param.data[self.lstm.hidden_size: 2 * self.lstm.hidden_size] = 1
            else:
                nn.init.xavier_uniform_(param)

    def forward(self, text, word_ids, word_mask, char_ids, label = None): # (batch_size, sen_len)
        embeds = None
        if self.use_word:
            word_emb = self.word_embeds(word_ids) # (batch_size, sen_len, 100)
            embeds = word_emb
        
        if self.use_char:
            char_emb = self.char_embeds(char_ids) # (batch_size, sen_len, max_sen_len, 30)
            if self.attention_pooling:
                char_emb = self.atten_pool(char_emb)
            else: # max pooling
                #print(char_emb.shape)
                char_emb = torch.max(char_emb, dim = 2).values # (batch_size, max_sen_len, embed_size)
                #print(char_emb.shape)
            if embeds is None:
                embeds = char_emb
            else:
                embeds = torch.cat((embeds, char_emb), dim = -1)
        
        if self.use_lm:
            lm_embeds = self.lm_embeds(text) # (batch_size, sen_len, 1024)
            if embeds is None:
                embeds = lm_embeds
            else:
                if embeds.shape[:2] != lm_embeds.shape[:2]:
                    print([len(i) for i in text], word_ids.shape)
                    print(embeds.shape, lm_embeds.shape)
                embeds = torch.cat((embeds, lm_embeds), dim = -1)
        
        embeds = self.dropout1(embeds)
        sen_len = torch.sum(word_mask, dim = 1, dtype = torch.int64).to('cpu') # (batch_size)
        pack_seq = pack_padded_sequence(embeds, sen_len, batch_first = True, enforce_sorted = False)
        lstm_out, _ = self.lstm(pack_seq)
        lstm_out, _ = pad_packed_sequence(lstm_out, batch_first = True) # (batch_size, seq_len, hidden_size)
        lstm_feats = self.hidden2tag(lstm_out) # （batch_size, seq_len, tagset_size)
        lstm_feats = self.dropout2(lstm_feats)
        
        if not self.use_crf:
            if label is not None:
                lstm_feats = self.dropout2(lstm_feats)
            return lstm_feats
        else:
            if label is None:
                predict = self.crf.viterbi_tags(lstm_feats, word_mask)
                return predict
            else:
                lstm_feats = self.dropout2(lstm_feats)
                log_likelihood = self.crf(lstm_feats, label, word_mask)
                batch_size = word_ids.shape[0]
                loss = -log_likelihood / batch_size
                return loss
