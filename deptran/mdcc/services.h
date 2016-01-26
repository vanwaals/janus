#include "deptran/config.h"
#include "deptran/command.h"
#include "deptran/command_marshaler.h"
#include "mdcc_rpc.h"
#include "MdccScheduler.h"

namespace mdcc {
  using rococo::Config;
  using rococo::Scheduler;
  using rococo::SimpleCommand;

  class MdccClientServiceImpl : public MdccClientService {
  protected:
    const Config::SiteInfo& my_site_info_;
    Config* const config_;
    MdccScheduler* const dtxn_mgr_;

  public:
    MdccClientServiceImpl() = delete;
    MdccClientServiceImpl(Config* config, uint32_t my_site_id, Scheduler* dtxn_mgr) :
        my_site_info_(config->SiteById(my_site_id)),
        config_(config),
        dtxn_mgr_(dynamic_cast<MdccScheduler*>(dtxn_mgr)) {
      dtxn_mgr_->init(config, my_site_id);
    }

    void Start(const StartRequest& req, StartResponse* res, rrr::DeferredReply* defer) override;
    void StartPiece(const SimpleCommand& cmd, StartPieceResponse* res, rrr::DeferredReply* defer) override;
  };
}
