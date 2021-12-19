import torch
import torch.nn as nn

class CharEmbedding(nn.Module):
    def __init__(self, n_chars, emb_dim, dropout = 0.5, use_cnn = True):
        super().__init__()
        self.n_chars = n_chars
        self.emb_dim = emb_dim
        self.use_cnn = use_cnn

        self.char_embeds = nn.Embedding(self.n_chars, emb_dim)
        if use_cnn:
            self.dropout = nn.Dropout(p = dropout)
            self.cnn = nn.Conv1d(in_channels = emb_dim, out_channels = emb_dim, kernel_size = 3, padding = 1)
        
    def _init(self):
        nn.init.kaiming_uniform_(self.char_embeds, mode = 'fan_out')
        if self.use_cnn:
            nn.init.xavier_uniform_(self.cnn.weight)
            nn.init.zeros_(self.cnn.bias)

    def forward(self, char_ids):
        '''
        input:
            char_ids: (batch_size, max_sen_len, max_word_len), dim = 3
                    (batch_size, max_sen_len), dim = 2
        output:
            char_embeds: (batch_size, max_sen_len, embed_size)
        '''
        dim = char_ids.dim()
        if dim == 2:
            char_ids = torch.unsqueeze(char_ids, dim = 1) # max_sen_len = 1
        batch_size, max_sen_len, max_word_len = char_ids.shape # (batch_size, max_sen_len, max_word_len)
        emb_size = self.emb_dim
        char_emb = self.char_embeds(char_ids) # (batch_size, max_sen_len, max_word_len, embed_size)
        if self.use_cnn:
            char_emb = char_emb.reshape(batch_size * max_sen_len, max_word_len, emb_size)
            char_emb = char_emb.permute(0, 2, 1) # (batch_size * max_sen_len, embed_size, max_word_len)
            char_emb = self.dropout(char_emb)
            char_emb = self.cnn(char_emb) # (batch_size * max_sen_len, embed_size, max_word_len + 2)
            char_emb = char_emb.reshape(batch_size, max_sen_len, -1, emb_size) # (batch_size, max_sen_len, max_word_len + 2, embed_size)
        if dim == 2:
            char_emb = torch.squeeze(char_emb, dim = 1)
        return char_emb